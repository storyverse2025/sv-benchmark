"""
Benchmark Pipeline v2 — Video Generation + Gemini VLM Scoring.

Three stages:
  A. Generate videos for sampled test cases (Seedance 2.0 + Kling v3)
  B. Build ground truth + VLM evaluation prompts
  C. Score each video with Gemini 3.1 Pro and compare against GT

Usage:
    # Set environment variables:
    #   GEMINI_API_KEY      — Gemini API key (OpenAI-compatible endpoint)
    #   GEMINI_API_BASE     — Gemini API base URL
    #   ARK_API_KEY         — Volcengine Seedance API key
    #   KLING_API_KEY       — Kling video generation API key
    #   KLING_API_BASE      — Kling API base URL
    python3 run_benchmark_v2.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── API config (from environment variables) ─────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_BASE = os.environ.get("GEMINI_API_BASE", "")
ARK_API_KEY = os.environ.get("ARK_API_KEY", "")
ARK_BASE = "https://ark.cn-beijing.volces.com"
ARK_TASKS_ENDPOINT = "/api/v3/contents/generations/tasks"
KLING_API_KEY = os.environ.get("KLING_API_KEY", "")
KLING_API_BASE = os.environ.get("KLING_API_BASE", "")

SEEDANCE_MODEL = "doubao-seedance-2-0-260128"
KLING_MODEL = "kling-v3"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")

KLING_VIDEO_ENDPOINT = "/v1/video/generations"
CHAT_ENDPOINT = "/v1/chat/completions"

POLL_INTERVAL = 15
MAX_POLL_TIME = 600

# ── Paths ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
TESTCASES_PATH = ROOT / "FlowDataPromptResults" / "flowdata_prompts_v4_gpt54_v2_and_videos" / "FlowData_2_compiled_testcases_v4_gpt54.json"
QC_PATH = ROOT / "FlowDataPromptResults" / "flowdata_prompts_v4_gpt54_v2_and_videos" / "FlowData_2_compiled_testcases_v4_gpt54_qc.json"
SYSTEM_PROMPT_PATH = ROOT / "analyzer" / "vlm_system_prompt.md"
GT_VALUES_PATH = ROOT / "analyzer" / "metrics_and_gt_values_list.txt"
OUTPUT_DIR = ROOT / "outputs" / "benchmark_v2"

SAMPLES_PER_LEVEL = 2


def _kling_headers() -> dict:
    return {
        "Authorization": f"Bearer {KLING_API_KEY}",
        "Content-Type": "application/json",
    }


def _gemini_headers() -> dict:
    return {
        "Authorization": f"Bearer {GEMINI_API_KEY}",
        "Content-Type": "application/json",
    }


# ====================================================================
# Stage A — Video Generation
# ====================================================================

def sample_testcases(testcases: list[dict]) -> list[dict]:
    by_level: dict[str, list[dict]] = {}
    for tc in testcases:
        level = tc["testcase_id"].split("-")[0]
        by_level.setdefault(level, []).append(tc)

    sampled = []
    for level in sorted(by_level):
        pool = by_level[level]
        n = min(SAMPLES_PER_LEVEL, len(pool))
        sampled.extend(random.sample(pool, n))
    logger.info("Sampled %d testcases: %s", len(sampled),
                [tc["testcase_id"] for tc in sampled])
    return sampled


def _ark_headers() -> dict:
    return {
        "Authorization": f"Bearer {ARK_API_KEY}",
        "Content-Type": "application/json",
    }


async def video_submit(
    client: httpx.AsyncClient, model: str, prompt: str, duration: int
) -> str:
    duration = max(4, min(15, duration))

    if model == SEEDANCE_MODEL:
        body = {
            "model": SEEDANCE_MODEL,
            "content": [{"type": "text", "text": prompt}],
            "duration": duration,
        }
        resp = await client.post(
            f"{ARK_BASE}{ARK_TASKS_ENDPOINT}",
            json=body,
            headers=_ark_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"Seedance submit returned no task ID: {data}")
        logger.info("[seedance] Submitted task %s (duration=%ds)", task_id, duration)
        return task_id

    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "duration": str(duration),
        "aspect_ratio": "16:9",
        "mode": "std",
    }
    resp = await client.post(
        f"{KLING_API_BASE}{KLING_VIDEO_ENDPOINT}",
        json=body,
        headers=_kling_headers(),
    )
    resp.raise_for_status()
    data = resp.json()
    task_id = data.get("task_id") or data.get("id")
    if not task_id:
        raise RuntimeError(f"No task_id in response: {data}")
    logger.info("[kling] Submitted task %s (duration=%ds)", task_id, duration)
    return task_id


def _extract_video_url(data: dict, model: str) -> Optional[str]:
    if model == SEEDANCE_MODEL:
        return data.get("content", {}).get("video_url")
    # Kling
    result_url = data.get("data", {}).get("result_url")
    if result_url:
        return result_url
    videos = (data.get("data", {}).get("data", {}).get("data", {})
              .get("task_result", {}).get("videos", []))
    if videos:
        return videos[0].get("url")
    return None


def _extract_status(data: dict) -> str:
    return data.get("data", {}).get("status", "UNKNOWN")


async def _poll_with_retry(client, url, headers, model_label, task_id):
    for retry in range(3):
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
            if retry < 2:
                await asyncio.sleep(5 * (retry + 1))
                continue
            raise


async def video_poll(
    client: httpx.AsyncClient, task_id: str, model: str
) -> Optional[str]:
    elapsed = 0

    if model == SEEDANCE_MODEL:
        url = f"{ARK_BASE}{ARK_TASKS_ENDPOINT}/{task_id}"
        while elapsed < MAX_POLL_TIME:
            data = await _poll_with_retry(client, url, _ark_headers(), "seedance", task_id)
            status = data.get("status", "")
            logger.info("[seedance] Task %s: %s", task_id[:20], status)
            if status == "succeeded":
                return data.get("content", {}).get("video_url")
            if status in ("running", "preparing", "queued"):
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL
                continue
            error_msg = data.get("error", {}).get("message", f"status={status}")
            logger.error("[seedance] Task %s failed: %s", task_id[:20], error_msg)
            return None
        logger.error("[seedance] Task %s timed out", task_id[:20])
        return None

    # Kling via Token Router
    url = f"{KLING_API_BASE}{KLING_VIDEO_ENDPOINT}/{task_id}"
    while elapsed < MAX_POLL_TIME:
        data = await _poll_with_retry(client, url, _kling_headers(), "kling", task_id)
        status = _extract_status(data)
        progress = data.get("data", {}).get("progress", "?")
        logger.info("[kling] Task %s: %s (%s)", task_id[:20], status, progress)

        if status == "SUCCESS":
            return _extract_video_url(data, model)
        if status in ("IN_PROGRESS", "NOT_START", "QUEUED", "SUBMITTED"):
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            continue
        if status == "FAILURE":
            reason = data.get("data", {}).get("fail_reason", "unknown")
            logger.error("[kling] Task %s failed: %s", task_id[:20], reason)
            return None
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL

    logger.error("[kling] Task %s timed out", task_id[:20])
    return None


async def download_video(
    client: httpx.AsyncClient, url: str, dest: Path
) -> bool:
    for retry in range(3):
        try:
            resp = await client.get(url, follow_redirects=True, timeout=120.0)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            logger.info("Downloaded %s (%d bytes)", dest.name, len(resp.content))
            return True
        except Exception as e:
            if retry < 2:
                logger.warning("Download retry %d for %s: %s", retry + 1, dest.name, e)
                await asyncio.sleep(5)
                continue
            logger.error("Download failed for %s: %s", dest.name, e)
            return False
    return False


async def generate_one(
    client: httpx.AsyncClient, tc: dict, model: str, video_dir: Path
) -> dict:
    tc_id = tc["testcase_id"]
    prompt = tc["final_video_prompt"]
    duration = tc.get("duration_seconds", 10)
    short = "seedance" if model == SEEDANCE_MODEL else "kling"

    task_id = await video_submit(client, model, prompt, duration)
    video_url = await video_poll(client, task_id, model)

    result = {
        "testcase_id": tc_id,
        "model": short,
        "task_id": task_id,
        "duration": duration,
        "video_path": None,
        "video_url": video_url,
    }
    if video_url:
        dest = video_dir / f"{tc_id}_{short}.mp4"
        if await download_video(client, video_url, dest):
            result["video_path"] = str(dest)

    status = "OK" if result["video_path"] else "FAIL"
    logger.info("=== %s [%s] %s ===", tc_id, short, status)
    return result


async def stage_a_generate_videos(
    testcases: list[dict],
) -> list[dict]:
    video_dir = OUTPUT_DIR / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)

    results = []
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(timeout=180.0, limits=limits) as client:
        # Submit and poll in batches of 4 to avoid connection overload
        all_jobs = []
        for tc in testcases:
            all_jobs.append((tc, SEEDANCE_MODEL))
            all_jobs.append((tc, KLING_MODEL))

        sem = asyncio.Semaphore(4)

        async def throttled(tc, model):
            async with sem:
                return await generate_one(client, tc, model, video_dir)

        results = await asyncio.gather(
            *(throttled(tc, model) for tc, model in all_jobs)
        )

    results_path = OUTPUT_DIR / "generation_results.json"
    with open(results_path, "w") as f:
        json.dump(list(results), f, indent=2, ensure_ascii=False)
    logger.info("Generation results → %s", results_path)

    ok = sum(1 for r in results if r["video_path"])
    logger.info("Video generation: %d/%d succeeded", ok, len(results))
    return list(results)


# ====================================================================
# Stage B — Ground Truth + VLM Prompts
# ====================================================================

def build_ground_truth(
    testcases: list[dict], qc_data: list[dict]
) -> list[dict]:
    sys.path.insert(0, str(ROOT / "analyzer"))
    from metrics_analyzer import build_records, parse_allowed_values

    allowed = parse_allowed_values(GT_VALUES_PATH)
    gt_records = build_records(testcases, qc_data, allowed)

    gt_path = OUTPUT_DIR / "metrics_ground_truth.json"
    with open(gt_path, "w") as f:
        json.dump(gt_records, f, indent=2, ensure_ascii=False)
    logger.info("Ground truth → %s (%d testcases)", gt_path, len(gt_records))
    return gt_records


def build_vlm_prompts(gt_records: list[dict]) -> list[dict]:
    sys.path.insert(0, str(ROOT / "analyzer"))
    from build_vlm_prompts import build_vlm_bundle

    bundles = [build_vlm_bundle(rec) for rec in gt_records]
    prompts_path = OUTPUT_DIR / "vlm_evaluation_prompts.json"
    with open(prompts_path, "w") as f:
        json.dump({
            "system_prompt_file": "vlm_system_prompt.md",
            "n_testcases": len(bundles),
            "bundles": bundles,
        }, f, indent=2, ensure_ascii=False)
    logger.info("VLM prompts → %s (%d bundles)", prompts_path, len(bundles))
    return bundles


# ====================================================================
# Stage C — Gemini VLM Scoring (native google.genai SDK, full video)
# ====================================================================

def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _init_gemini_client():
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)


def _upload_video(gemini_client, video_path: str) -> Any:
    import time as _time
    uploaded = gemini_client.files.upload(file=video_path)
    while uploaded.state.name == "PROCESSING":
        _time.sleep(3)
        uploaded = gemini_client.files.get(name=uploaded.name)
    if uploaded.state.name != "ACTIVE":
        raise RuntimeError(f"Video upload failed: {uploaded.state}")
    return uploaded


def _parse_gemini_json(content: str) -> dict:
    import re as _re
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    content = _re.sub(r',\s*}', '}', content)
    content = _re.sub(r',\s*\]', ']', content)
    return json.loads(content)


def score_one_video(
    gemini_client,
    video_file,
    user_prompt: str,
    system_prompt: str,
    tc_id: str,
    model_name: str,
) -> Optional[dict]:
    from google import genai
    from google.genai import types

    logger.info("[gemini] Scoring %s (%s)...", tc_id, model_name)

    for attempt in range(3):
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=user_prompt),
                            types.Part.from_uri(
                                file_uri=video_file.uri,
                                mime_type="video/mp4",
                            ),
                        ],
                    ),
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0,
                    max_output_tokens=4000,
                ),
            )

            content = response.text
            if not content:
                logger.warning("[gemini] Empty content for %s (%s), retrying...",
                               tc_id, model_name)
                if attempt < 2:
                    time.sleep(5)
                    continue
                return None

            prediction = _parse_gemini_json(content)
            usage = response.usage_metadata
            video_tokens = 0
            if usage and usage.prompt_tokens_details:
                for detail in usage.prompt_tokens_details:
                    if detail.modality.name == "VIDEO":
                        video_tokens = detail.token_count
            total = usage.total_token_count if usage else 0
            logger.info("[gemini] %s (%s): %d metrics, %d total tokens (%d video tokens)",
                        tc_id, model_name, len(prediction), total, video_tokens)
            return prediction

        except json.JSONDecodeError as e:
            logger.error("[gemini] JSON parse error for %s (%s): %s\nRaw: %s",
                         tc_id, model_name, e, content[:300])
            if attempt < 2:
                time.sleep(5)
                continue
            return None
        except Exception as e:
            logger.error("[gemini] Error scoring %s (%s): %s",
                         tc_id, model_name, e)
            if attempt < 2:
                time.sleep(10)
                continue
            return None

    return None


async def stage_c_score_videos(
    gen_results: list[dict],
    bundles: list[dict],
    gt_records: list[dict],
) -> dict:
    sys.path.insert(0, str(ROOT / "analyzer"))
    from compare_predictions_vs_gt import compare_testcase, print_report, print_overall

    system_prompt = _load_system_prompt()
    bundle_by_id = {b["testcase_id"]: b for b in bundles}
    gt_by_id = {r["testcase_id"]: r for r in gt_records}

    seedance_preds: dict[str, dict] = {}
    kling_preds: dict[str, dict] = {}

    gemini_client = _init_gemini_client()

    # Upload all videos first, then score
    video_files: dict[str, Any] = {}
    for gen in gen_results:
        if not gen.get("video_path"):
            continue
        vpath = gen["video_path"]
        if vpath not in video_files:
            logger.info("[gemini] Uploading %s...", Path(vpath).name)
            video_files[vpath] = _upload_video(gemini_client, vpath)

    for gen in gen_results:
        if not gen.get("video_path"):
            continue

        tc_id = gen["testcase_id"]
        model_name = gen["model"]
        bundle = bundle_by_id.get(tc_id)
        if not bundle:
            logger.warning("No VLM bundle for %s, skipping", tc_id)
            continue

        vfile = video_files.get(gen["video_path"])
        if not vfile:
            continue

        pred = score_one_video(
            gemini_client, vfile,
            bundle["user_prompt"], system_prompt,
            tc_id, model_name,
        )
        if pred:
            if model_name == "seedance":
                seedance_preds[tc_id] = pred
            else:
                kling_preds[tc_id] = pred

        time.sleep(2)

    # Save predictions
    for name, preds in [("seedance", seedance_preds), ("kling", kling_preds)]:
        path = OUTPUT_DIR / f"vlm_predictions_{name}.json"
        with open(path, "w") as f:
            json.dump(preds, f, indent=2, ensure_ascii=False)
        logger.info("Predictions → %s (%d testcases)", path, len(preds))

    # Compare and generate reports
    all_reports = {}
    for name, preds in [("seedance", seedance_preds), ("kling", kling_preds)]:
        reports = []
        for tc_id, pred in preds.items():
            gt_rec = gt_by_id.get(tc_id)
            if not gt_rec:
                continue
            report = compare_testcase(gt_rec, pred)
            reports.append(report)
            print_report(report)

        print_overall(reports)

        report_path = OUTPUT_DIR / f"comparison_report_{name}.json"
        with open(report_path, "w") as f:
            json.dump({
                "model": name,
                "n_testcases": len(reports),
                "reports": reports,
            }, f, indent=2, ensure_ascii=False)
        logger.info("Comparison report → %s", report_path)
        all_reports[name] = reports

    # Summary
    summary = _build_summary(all_reports)
    summary_path = OUTPUT_DIR / "summary_report.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Summary → %s", summary_path)
    return summary


def _build_summary(all_reports: dict) -> dict:
    summary = {"models": {}}
    for model_name, reports in all_reports.items():
        if not reports:
            summary["models"][model_name] = {"n_testcases": 0}
            continue

        n = len(reports)
        tot_scorable = sum(r["counts"]["scorable"] for r in reports)
        tot_exact = sum(r["counts"]["exact"] for r in reports)
        avg_exact = tot_exact / tot_scorable if tot_scorable else 0
        avg_macro_f1 = sum(r["aggregate"]["macro_f1"] for r in reports) / n
        avg_micro_f1 = sum(r["aggregate"]["micro_f1"] for r in reports) / n

        per_tc = []
        for r in reports:
            per_tc.append({
                "testcase_id": r["testcase_id"],
                "difficulty": r["difficulty"],
                "exact_match_rate": r["aggregate"]["exact_match_rate"],
                "macro_f1": r["aggregate"]["macro_f1"],
                "micro_f1": r["aggregate"]["micro_f1"],
            })

        summary["models"][model_name] = {
            "n_testcases": n,
            "global_exact_match_rate": avg_exact,
            "mean_macro_f1": avg_macro_f1,
            "mean_micro_f1": avg_micro_f1,
            "per_testcase": per_tc,
        }
    return summary


# ====================================================================
# Main
# ====================================================================

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-video-gen", action="store_true",
                        help="Skip video generation, reuse existing videos")
    args = parser.parse_args()

    random.seed(42)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load test cases
    with open(TESTCASES_PATH) as f:
        all_testcases = json.load(f)
    with open(QC_PATH) as f:
        qc_data = json.load(f)
    logger.info("Loaded %d testcases, %d QC records", len(all_testcases), len(qc_data))

    # Sample (same seed = same selection)
    testcases = sample_testcases(all_testcases)

    if args.skip_video_gen:
        # Rebuild gen_results from existing files
        gen_results_path = OUTPUT_DIR / "generation_results.json"
        if gen_results_path.exists():
            with open(gen_results_path) as f:
                gen_results = json.load(f)
            logger.info("Loaded %d generation results from previous run", len(gen_results))
        else:
            # Reconstruct from video files
            video_dir = OUTPUT_DIR / "videos"
            gen_results = []
            for tc in testcases:
                tc_id = tc["testcase_id"]
                for short in ["seedance", "kling"]:
                    vpath = video_dir / f"{tc_id}_{short}.mp4"
                    gen_results.append({
                        "testcase_id": tc_id,
                        "model": short,
                        "video_path": str(vpath) if vpath.exists() else None,
                    })
            logger.info("Reconstructed %d gen results from files", len(gen_results))
    else:
        # Stage A
        logger.info("=" * 60)
        logger.info("STAGE A: VIDEO GENERATION")
        logger.info("=" * 60)
        gen_results = await stage_a_generate_videos(testcases)

    # Stage B
    logger.info("=" * 60)
    logger.info("STAGE B: GROUND TRUTH + VLM PROMPTS")
    logger.info("=" * 60)
    gt_records = build_ground_truth(testcases, qc_data)
    bundles = build_vlm_prompts(gt_records)

    # Stage C
    logger.info("=" * 60)
    logger.info("STAGE C: GEMINI VLM SCORING")
    logger.info("=" * 60)
    summary = await stage_c_score_videos(gen_results, bundles, gt_records)

    # Final summary
    print("\n" + "=" * 80)
    print("BENCHMARK v2 FINAL SUMMARY")
    print("=" * 80)
    for model_name, stats in summary.get("models", {}).items():
        print(f"\n{'─' * 40}")
        print(f"Model: {model_name.upper()}")
        print(f"  Testcases scored: {stats['n_testcases']}")
        if stats["n_testcases"]:
            print(f"  Global exact-match rate: {stats['global_exact_match_rate']*100:.1f}%")
            print(f"  Mean macro F1: {stats['mean_macro_f1']:.3f}")
            print(f"  Mean micro F1: {stats['mean_micro_f1']:.3f}")
            print(f"  Per testcase:")
            for tc in stats["per_testcase"]:
                print(f"    {tc['testcase_id']} ({tc['difficulty']}): "
                      f"exact={tc['exact_match_rate']*100:.1f}% "
                      f"macro_f1={tc['macro_f1']:.3f} "
                      f"micro_f1={tc['micro_f1']:.3f}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
