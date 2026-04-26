"""
Scoring v3 — Gemini 3.1 Pro full-video evaluation with Top-3 confidence prompt.

Uses native google.genai Files API for full video understanding.
Uses the latest vlm_system_prompt.md (top-3 ranked candidates + confidence).

Usage:
    export GEMINI_API_KEY="your-key"
    python3 run_scoring_v3.py
    python3 run_scoring_v3.py --cot   # use CoT prompt instead
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
ANALYZER_DIR = ROOT / "analyzer"
OUTPUT_DIR = ROOT / "outputs" / "benchmark_v2"
VIDEO_DIR = OUTPUT_DIR / "videos"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")


def load_system_prompt(cot: bool) -> str:
    if cot:
        path = ANALYZER_DIR / "vlm_system_prompt_cot.md"
    else:
        path = ANALYZER_DIR / "vlm_system_prompt.md"
    return path.read_text(encoding="utf-8")


def load_bundles() -> dict:
    path = OUTPUT_DIR / "vlm_evaluation_prompts.json"
    with open(path) as f:
        data = json.load(f)
    return {b["testcase_id"]: b for b in data["bundles"]}


def load_gt() -> dict:
    path = OUTPUT_DIR / "metrics_ground_truth.json"
    with open(path) as f:
        records = json.load(f)
    return {r["testcase_id"]: r for r in records}


def init_gemini():
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)


def upload_video(client, video_path: str):
    for attempt in range(2):
        try:
            uploaded = client.files.upload(file=video_path)
            while uploaded.state.name == "PROCESSING":
                time.sleep(3)
                uploaded = client.files.get(name=uploaded.name)
            if uploaded.state.name == "ACTIVE":
                return uploaded
            raise RuntimeError(f"Video upload state: {uploaded.state}")
        except Exception as e:
            if attempt < 1:
                logger.warning("Upload retry for %s: %s", os.path.basename(video_path), e)
                time.sleep(10)
                continue
            raise


def parse_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    content = re.sub(r',\s*}', '}', content)
    content = re.sub(r',\s*\]', ']', content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Try to repair truncated JSON by closing open brackets
        repaired = content
        open_braces = repaired.count('{') - repaired.count('}')
        open_brackets = repaired.count('[') - repaired.count(']')
        # Trim trailing incomplete value
        repaired = re.sub(r',\s*"[^"]*$', '', repaired)
        repaired = re.sub(r',\s*\{[^}]*$', '', repaired)
        repaired = re.sub(r',\s*"value":\s*"[^"]*$', '', repaired)
        # Recount and close
        open_brackets = repaired.count('[') - repaired.count(']')
        open_braces = repaired.count('{') - repaired.count('}')
        repaired += ']' * max(0, open_brackets)
        repaired += '}' * max(0, open_braces)
        repaired = re.sub(r',\s*}', '}', repaired)
        repaired = re.sub(r',\s*\]', ']', repaired)
        return json.loads(repaired)


def score_video(client, video_file, user_prompt: str, system_prompt: str,
                tc_id: str, model_name: str) -> Optional[dict]:
    from google.genai import types

    logger.info("[gemini] Scoring %s (%s)...", tc_id, model_name)

    for attempt in range(3):
        try:
            response = client.models.generate_content(
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
                    max_output_tokens=32000,
                ),
            )

            content = response.text
            if not content:
                logger.warning("[gemini] Empty content for %s (%s), retry %d",
                               tc_id, model_name, attempt + 1)
                if attempt < 2:
                    time.sleep(5)
                    continue
                return None

            prediction = parse_response(content)

            usage = response.usage_metadata
            video_tokens = 0
            if usage and usage.prompt_tokens_details:
                for detail in usage.prompt_tokens_details:
                    if detail.modality.name == "VIDEO":
                        video_tokens = detail.token_count
            total = usage.total_token_count if usage else 0
            n_metrics = len([k for k in prediction if k != "reasoning"])
            logger.info("[gemini] %s (%s): %d metrics, %d total tokens (%d video)",
                        tc_id, model_name, n_metrics, total, video_tokens)
            return prediction

        except json.JSONDecodeError as e:
            logger.error("[gemini] JSON error %s (%s): %s\nRaw: %s",
                         tc_id, model_name, e, content[:300] if content else "")
            if attempt < 2:
                time.sleep(5)
                continue
            return None
        except Exception as e:
            logger.error("[gemini] Error %s (%s): %s", tc_id, model_name, e)
            if attempt < 2:
                time.sleep(10)
                continue
            return None
    return None


def extract_top1(prediction: dict) -> dict:
    """Extract top-1 value from each metric for backward-compatible comparison."""
    result = {}
    for key, val in prediction.items():
        if key == "reasoning":
            continue
        if isinstance(val, list) and val and isinstance(val[0], dict):
            top1_values = []
            for item in val:
                v = item.get("value", "unpredictable")
                c = item.get("confidence", 0)
                if c >= 0.5 and v != "unpredictable":
                    top1_values.append(v)
            if not top1_values and val:
                top1_values = [val[0].get("value", "unpredictable")]
            result[key] = top1_values
        else:
            result[key] = val
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cot", action="store_true", help="Use CoT system prompt")
    args = parser.parse_args()

    suffix = "_cot" if args.cot else ""
    system_prompt = load_system_prompt(args.cot)
    bundles = load_bundles()
    gt_by_id = load_gt()

    logger.info("System prompt: %s (%d chars)", "CoT" if args.cot else "non-CoT", len(system_prompt))
    logger.info("Bundles: %d, GT: %d", len(bundles), len(gt_by_id))

    gemini = init_gemini()

    # Discover videos
    videos = sorted(VIDEO_DIR.glob("*.mp4"))
    logger.info("Found %d videos", len(videos))

    # Upload-then-score one at a time to avoid 503s from bulk uploads
    all_predictions = {}
    for i, vpath in enumerate(videos):
        name = vpath.stem
        parts = name.rsplit("_", 1)
        if len(parts) != 2:
            logger.warning("Skipping %s (can't parse tc_id/model)", name)
            continue
        tc_id, model_name = parts

        bundle = bundles.get(tc_id)
        if not bundle:
            logger.warning("No bundle for %s, skipping", tc_id)
            continue

        logger.info("Uploading %s (%d/%d)...", vpath.name, i + 1, len(videos))
        try:
            vfile = upload_video(gemini, str(vpath))
        except Exception as e:
            logger.error("Upload failed permanently for %s: %s", vpath.name, e)
            continue

        pred = score_video(gemini, vfile, bundle["user_prompt"], system_prompt,
                           tc_id, model_name)
        if pred:
            all_predictions.setdefault(tc_id, {})[model_name] = pred

        # Clean up uploaded file
        try:
            gemini.files.delete(name=vfile.name)
        except Exception:
            pass

        time.sleep(3)

    # Save raw predictions (top-3 format)
    for model_name in ["seedance", "kling"]:
        preds = {tc_id: preds[model_name]
                 for tc_id, preds in all_predictions.items()
                 if model_name in preds}
        path = OUTPUT_DIR / f"vlm_predictions_{model_name}_v3{suffix}.json"
        with open(path, "w") as f:
            json.dump(preds, f, indent=2, ensure_ascii=False)
        logger.info("Raw predictions → %s (%d)", path, len(preds))

    # Compare using updated compare logic with confidence thresholds
    sys.path.insert(0, str(ANALYZER_DIR))
    from compare_predictions_vs_gt import score_testcase, print_report, print_overall

    THRESHOLD = 0.5
    TOP_K = 1
    WEIGHTED = False

    all_reports = {}
    for model_name in ["seedance", "kling"]:
        reports = []
        for tc_id, preds in all_predictions.items():
            if model_name not in preds:
                continue
            gt_rec = gt_by_id.get(tc_id)
            if not gt_rec:
                continue

            pred = preds[model_name]
            # Strip reasoning key if present
            pred_clean = {k: v for k, v in pred.items() if k != "reasoning"}
            report = score_testcase(gt_rec, pred_clean,
                                    threshold=THRESHOLD, top_k=TOP_K, weighted=WEIGHTED)
            reports.append(report)
            print_report(report)

        if reports:
            print_overall(reports)
        all_reports[model_name] = reports

        report_path = OUTPUT_DIR / f"comparison_report_{model_name}_v3{suffix}.json"
        with open(report_path, "w") as f:
            json.dump({"model": model_name, "n_testcases": len(reports),
                        "confidence_threshold": THRESHOLD, "top_k": TOP_K,
                        "reports": reports}, f, indent=2, ensure_ascii=False)
        logger.info("Comparison → %s", report_path)

    # Summary
    print("\n" + "=" * 80)
    print(f"SCORING v3 SUMMARY {'(CoT)' if args.cot else '(non-CoT)'}")
    print(f"Confidence threshold: {THRESHOLD}, Top-K: {TOP_K}")
    print("=" * 80)
    for model_name, reports in all_reports.items():
        if not reports:
            continue
        n = len(reports)
        n_scorable = sum(r.get("n_scorable", 0) for r in reports)
        acc = sum(r.get("accuracy", 0) for r in reports) / n if n else 0
        prec = sum(r.get("precision", 0) for r in reports) / n if n else 0
        rec = sum(r.get("recall", 0) for r in reports) / n if n else 0
        print(f"\n{model_name.upper()} ({n} testcases):")
        print(f"  Mean Accuracy:  {acc*100:.1f}%")
        print(f"  Mean Precision: {prec:.3f}")
        print(f"  Mean Recall:    {rec:.3f}")
    print("=" * 80)


if __name__ == "__main__":
    main()
