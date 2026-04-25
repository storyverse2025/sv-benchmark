"""
analyzer/score_videos_with_gemini.py

Standalone Gemini VLM scoring script — distilled from
run_benchmark_v2.py Stage C (commit 64a58e4: Token Router variant).

Given one or more local mp4 files, this script:
    1. Loads the shared system prompt (vlm_system_prompt.md).
    2. Looks up the per-testcase user_prompt from vlm_evaluation_prompts.json
       by parsing the testcase_id out of the video filename.
    3. Calls Gemini 3.1 Pro through an OpenAI-compatible Token Router
       (default: OpenRouter) with the video inlined as a base64
       data URL inside a `video_url` content part.
    4. Parses the JSON object the model returns and writes one
       prediction file (and optionally compares against GT).

Filename convention assumed (matches run_benchmark_v2.py output):
    <testcase_id>_<model>.mp4
e.g. S1-2-cyberpunk-beach-vehicle-water-splash-driving_seedance.mp4
        -> testcase_id=S1-2-cyberpunk-beach-vehicle-water-splash-driving
           model=seedance

Environment variables (CLI flags override):
    GEMINI_API_KEY    Token Router API key (sk-...)
    GEMINI_API_BASE   default https://openrouter.ai/api/v1
    GEMINI_MODEL      default google/gemini-3.1-pro-preview

Quick start:
    export GEMINI_API_KEY=sk-...
    python analyzer/score_videos_with_gemini.py \
        --video FlowDataPromptResults/0results/benchmark_v2/videos/\
S1-2-cyberpunk-beach-vehicle-water-splash-driving_seedance.mp4 \
        --compare
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Paths & defaults
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent  # sv-benchmark/
ANALYZER_DIR = ROOT / "analyzer"
OUTPUT_DIR = ROOT / "outputs" / "benchmark_v2"

DEFAULT_BUNDLES = OUTPUT_DIR / "vlm_evaluation_prompts.json"
DEFAULT_SYSTEM_PROMPT = ANALYZER_DIR / "vlm_system_prompt.md"
DEFAULT_GT = OUTPUT_DIR / "metrics_ground_truth.json"

DEFAULT_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-3.1-pro-preview"

CHAT_ENDPOINT = "/chat/completions"  # base URL already ends with /v1

MAX_RETRIES = 3
REQUEST_TIMEOUT = 300.0  # seconds — videos can be large

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("score_videos")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

KNOWN_MODEL_SUFFIXES = ("seedance", "kling", "veo", "runway", "pika", "sora")


def parse_testcase_and_model(video_path: Path) -> tuple[str, str]:
    """Split `S1-2-foo-bar_seedance.mp4` into (`S1-2-foo-bar`, `seedance`).

    Falls back to (stem, "unknown") if no known suffix is found.
    """
    stem = video_path.stem  # drops .mp4
    for suffix in KNOWN_MODEL_SUFFIXES:
        token = f"_{suffix}"
        if stem.endswith(token):
            return stem[: -len(token)], suffix
    if "_" in stem:
        tc_id, model_name = stem.rsplit("_", 1)
        return tc_id, model_name
    return stem, "unknown"


def load_bundles(path: Path) -> dict[str, dict]:
    """Index VLM bundles by testcase_id."""
    data = json.loads(path.read_text(encoding="utf-8"))
    bundles = data.get("bundles", data)  # support raw list or wrapped dict
    if isinstance(bundles, dict):
        bundles = list(bundles.values())
    return {b["testcase_id"]: b for b in bundles}


def load_gt(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("testcases", data)
    if isinstance(records, dict):
        records = list(records.values())
    return {r["testcase_id"]: r for r in records}


def encode_video_data_url(video_path: Path) -> str:
    raw = video_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:video/mp4;base64,{b64}"


def strip_json_fences(content: str) -> str:
    """Gemini sometimes wraps JSON in ```json ... ``` and adds trailing commas."""
    s = content.strip()
    if s.startswith("```"):
        # drop ``` or ```json line
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[: -3]
        s = s.strip()
    s = re.sub(r",\s*}", "}", s)
    s = re.sub(r",\s*]", "]", s)
    return s


# ---------------------------------------------------------------------------
# Gemini scoring
# ---------------------------------------------------------------------------


def _video_part(video_data_url: str, kind: str) -> dict:
    """Build the multimodal video content part for the chosen router style.

    OpenRouter currently accepts several shapes for video; not every backend
    model translates each shape, so we keep them swappable from the CLI.
    """
    if kind == "video_url":
        return {"type": "video_url", "video_url": {"url": video_data_url}}
    if kind == "image_url":
        return {"type": "image_url", "image_url": {"url": video_data_url}}
    if kind == "file":
        return {
            "type": "file",
            "file": {"file_data": video_data_url, "filename": "video.mp4"},
        }
    raise ValueError(f"unknown video content kind: {kind!r}")


def build_messages(
    system_prompt: str,
    user_prompt: str,
    video_data_url: str,
    *,
    video_content_kind: str = "video_url",
) -> list[dict]:
    """OpenRouter / OpenAI-compatible content layout for a chat completion."""
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                _video_part(video_data_url, video_content_kind),
            ],
        },
    ]


def score_one_video(
    client: httpx.Client,
    *,
    api_base: str,
    model: str,
    api_key: str,
    video_path: Path,
    user_prompt: str,
    system_prompt: str,
    tc_id: str,
    model_label: str,
    extra_headers: dict[str, str] | None = None,
    video_content_kind: str = "video_url",
) -> dict | None:
    logger.info("[gemini] scoring %s (%s) ...", tc_id, model_label)
    video_url = encode_video_data_url(video_path)
    payload = {
        "model": model,
        "messages": build_messages(
            system_prompt, user_prompt, video_url,
            video_content_kind=video_content_kind,
        ),
        "max_tokens": 4000,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    url = api_base.rstrip("/") + CHAT_ENDPOINT

    last_raw: str = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.error(
                    "[gemini] HTTP %d for %s (attempt %d/%d): %s",
                    resp.status_code, tc_id, attempt, MAX_RETRIES, resp.text[:300],
                )
                if attempt < MAX_RETRIES:
                    time.sleep(5 * attempt)
                    continue
                return None

            data = resp.json()
            if "error" in data:
                logger.error("[gemini] API error for %s: %s", tc_id, data["error"])
                if attempt < MAX_RETRIES:
                    time.sleep(5 * attempt)
                    continue
                return None

            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            last_raw = content
            if not content:
                logger.warning(
                    "[gemini] empty content for %s (attempt %d/%d)",
                    tc_id, attempt, MAX_RETRIES,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(5)
                    continue
                return None

            cleaned = strip_json_fences(content)
            prediction = json.loads(cleaned)
            usage = data.get("usage", {}) or {}
            logger.info(
                "[gemini] %s ok: %d metrics predicted, %d tokens (prompt=%d, completion=%d)",
                tc_id,
                len(prediction),
                usage.get("total_tokens", 0),
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
            return prediction

        except json.JSONDecodeError as e:
            logger.error(
                "[gemini] JSON parse error for %s (attempt %d/%d): %s\n  raw: %s",
                tc_id, attempt, MAX_RETRIES, e, last_raw[:300],
            )
            if attempt < MAX_RETRIES:
                time.sleep(5)
                continue
            return None
        except httpx.HTTPError as e:
            logger.error(
                "[gemini] network error for %s (attempt %d/%d): %s",
                tc_id, attempt, MAX_RETRIES, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(5 * attempt)
                continue
            return None

    return None


# ---------------------------------------------------------------------------
# Optional: compare against GT
# ---------------------------------------------------------------------------


def maybe_compare(predictions: dict[str, dict], gt_by_id: dict[str, dict]) -> None:
    """Use compare_predictions_vs_gt to print a per-testcase + overall report."""
    sys.path.insert(0, str(ANALYZER_DIR))
    try:
        from compare_predictions_vs_gt import (  # type: ignore
            compare_testcase,
            print_overall,
            print_report,
        )
    except ImportError as e:
        logger.warning("Cannot import compare_predictions_vs_gt: %s", e)
        return

    reports: list[dict[str, Any]] = []
    for tc_id, pred in predictions.items():
        gt_record = gt_by_id.get(tc_id)
        if not gt_record:
            logger.warning("No GT record for %s, skipping comparison", tc_id)
            continue
        report = compare_testcase(gt_record, pred)
        reports.append(report)
        print_report(report)

    if len(reports) > 1:
        print_overall(reports)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Score benchmark videos with Gemini via Token Router.",
    )
    p.add_argument(
        "--video", action="append", required=True,
        help="Path to an mp4 to score. Pass --video multiple times for batch.",
    )
    p.add_argument(
        "--bundles", type=Path, default=DEFAULT_BUNDLES,
        help=f"VLM bundles JSON. Default: {DEFAULT_BUNDLES.relative_to(ROOT)}",
    )
    p.add_argument(
        "--system-prompt", type=Path, default=DEFAULT_SYSTEM_PROMPT,
        help=f"System prompt md. Default: {DEFAULT_SYSTEM_PROMPT.relative_to(ROOT)}",
    )
    p.add_argument(
        "--gt", type=Path, default=DEFAULT_GT,
        help=f"Ground truth JSON for --compare. Default: {DEFAULT_GT.relative_to(ROOT)}",
    )
    p.add_argument(
        "--out", type=Path, default=None,
        help="Output predictions JSON. Default: outputs/benchmark_v2/"
             "vlm_predictions_<model>_<ts>.json",
    )
    p.add_argument(
        "--api-key", default=os.environ.get("GEMINI_API_KEY", ""),
        help="Token Router API key. Falls back to env GEMINI_API_KEY.",
    )
    p.add_argument(
        "--api-base", default=os.environ.get("GEMINI_API_BASE", DEFAULT_API_BASE),
        help=f"Token Router base URL. Default: {DEFAULT_API_BASE}",
    )
    p.add_argument(
        "--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        help=f"Model name. Default: {DEFAULT_MODEL}",
    )
    p.add_argument(
        "--compare", action="store_true",
        help="After scoring, compare predictions against --gt and print report.",
    )
    p.add_argument(
        "--app-name", default="sv-benchmark",
        help="Sent as X-Title header (OpenRouter convention).",
    )
    p.add_argument(
        "--video-content-type",
        choices=("video_url", "image_url", "file"),
        default="video_url",
        help=(
            "How to attach the video in the chat content. Try 'image_url' "
            "or 'file' if OpenRouter rejects 'video_url' for this model."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not args.api_key:
        logger.error(
            "No API key. Set --api-key or export GEMINI_API_KEY=sk-... before running.",
        )
        return 2

    bundles_by_id = load_bundles(args.bundles)
    system_prompt = args.system_prompt.read_text(encoding="utf-8")

    extra_headers = {
        # OpenRouter recommended (optional) headers; harmless on other proxies.
        "HTTP-Referer": "https://github.com/storyverse2025/sv-benchmark",
        "X-Title": args.app_name,
    }

    predictions: dict[str, dict] = {}
    model_labels: set[str] = set()

    with httpx.Client() as client:
        for vstr in args.video:
            video_path = Path(vstr).expanduser().resolve()
            if not video_path.exists():
                logger.error("Video not found: %s", video_path)
                continue

            tc_id, model_label = parse_testcase_and_model(video_path)
            model_labels.add(model_label)

            bundle = bundles_by_id.get(tc_id)
            if not bundle:
                logger.error(
                    "No VLM bundle for testcase_id=%s (file=%s). "
                    "Available: %d bundles. Skipping.",
                    tc_id, video_path.name, len(bundles_by_id),
                )
                continue

            user_prompt = bundle["user_prompt"]
            size_mb = video_path.stat().st_size / (1024 * 1024)
            logger.info(
                "→ %s [%s] (%.1f MiB), %d scorable metrics",
                tc_id, model_label, size_mb, bundle.get("num_scorable_metrics", -1),
            )

            pred = score_one_video(
                client,
                api_base=args.api_base,
                model=args.model,
                api_key=args.api_key,
                video_path=video_path,
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                tc_id=tc_id,
                model_label=model_label,
                extra_headers=extra_headers,
                video_content_kind=args.video_content_type,
            )
            if pred:
                predictions[tc_id] = pred

    if not predictions:
        logger.error("No successful predictions.")
        return 1

    out_path = args.out
    if out_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        label = "_".join(sorted(model_labels)) or "model"
        out_path = OUTPUT_DIR / f"vlm_predictions_{label}_{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(predictions, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Wrote %d prediction(s) → %s", len(predictions), out_path)

    if args.compare:
        if not args.gt.exists():
            logger.warning("GT file %s missing, skipping compare", args.gt)
        else:
            gt_by_id = load_gt(args.gt)
            maybe_compare(predictions, gt_by_id)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
