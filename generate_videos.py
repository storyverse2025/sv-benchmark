"""
Generate benchmark videos from compiled_testcases.json using Kling and Seedance APIs.

Usage:
    1. Create a .env file with:
       KLING_ACCESS_KEY=xxx
       KLING_SECRET_KEY=xxx
       ARK_API_KEY=xxx
    2. pip install httpx pyjwt python-dotenv
    3. python generate_videos.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KLING_ACCESS_KEY = os.environ["KLING_ACCESS_KEY"]
KLING_SECRET_KEY = os.environ["KLING_SECRET_KEY"]
ARK_API_KEY = os.environ["ARK_API_KEY"]

KLING_API_BASE = "https://api-beijing.klingai.com"
KLING_VIDEO_ENDPOINT = "/v1/videos/omni-video"

ARK_API_BASE = "https://ark.cn-beijing.volces.com"
ARK_TASKS_ENDPOINT = "/api/v3/contents/generations/tasks"
SEEDANCE_MODEL = "doubao-seedance-2-0-260128"

OUTPUT_DIR = Path(__file__).parent / "outputs" / "videos"
POLL_INTERVAL = 15  # seconds between status checks
MAX_POLL_TIME = 600  # 10 minutes max wait per task

TESTCASES_PATH = Path(__file__).parent / "outputs" / "compiled_testcases.json"

# ---------------------------------------------------------------------------
# Kling helpers
# ---------------------------------------------------------------------------

def _kling_jwt() -> str:
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": KLING_ACCESS_KEY,
        "exp": int(time.time()) + 1800,
        "nbf": int(time.time()) - 5,
    }
    return jwt.encode(payload, KLING_SECRET_KEY, algorithm="HS256", headers=headers)


def _kling_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_kling_jwt()}",
        "Content-Type": "application/json",
    }


async def kling_submit(client: httpx.AsyncClient, prompt: str, duration: int) -> str:
    body = {
        "model_name": "kling-v3-omni",
        "prompt": prompt,
        "mode": "std",
        "duration": str(duration),
        "aspect_ratio": "16:9",
    }
    resp = await client.post(
        f"{KLING_API_BASE}{KLING_VIDEO_ENDPOINT}",
        json=body,
        headers=_kling_headers(),
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Kling submit error {data.get('code')}: {data.get('message')}")
    task_id = data["data"]["task_id"]
    logger.info("[kling] Submitted task %s", task_id)
    return task_id


async def kling_poll(client: httpx.AsyncClient, task_id: str) -> dict | None:
    """Poll until succeed/failed. Returns task_result dict or None."""
    url = f"{KLING_API_BASE}{KLING_VIDEO_ENDPOINT}/{task_id}"
    elapsed = 0
    while elapsed < MAX_POLL_TIME:
        resp = await client.get(url, headers=_kling_headers())
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.error("[kling] Poll error for %s: %s", task_id, data.get("message"))
            return None
        status = data["data"].get("task_status", "")
        logger.info("[kling] Task %s status: %s", task_id, status)
        if status == "succeed":
            return data["data"].get("task_result", {})
        if status not in ("processing", "submitted"):
            logger.error("[kling] Task %s failed: %s", task_id, data["data"].get("task_status_msg"))
            return None
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    logger.error("[kling] Task %s timed out after %ds", task_id, MAX_POLL_TIME)
    return None


# ---------------------------------------------------------------------------
# Seedance (Volcengine) helpers
# ---------------------------------------------------------------------------

def _ark_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ARK_API_KEY}",
        "Content-Type": "application/json",
    }


async def seedance_submit(client: httpx.AsyncClient, prompt: str, duration: int = 5) -> str:
    # Seedance 2.0 supports 4-15s duration
    duration = max(4, min(15, duration))
    body = {
        "model": SEEDANCE_MODEL,
        "content": [{"type": "text", "text": prompt}],
        "duration": duration,
    }
    resp = await client.post(
        f"{ARK_API_BASE}{ARK_TASKS_ENDPOINT}",
        json=body,
        headers=_ark_headers(),
    )
    resp.raise_for_status()
    data = resp.json()
    task_id = data.get("id")
    if not task_id:
        raise RuntimeError(f"Seedance submit returned no task ID: {data}")
    logger.info("[seedance] Submitted task %s", task_id)
    return task_id


async def seedance_poll(client: httpx.AsyncClient, task_id: str) -> str | None:
    """Poll until succeeded/failed. Returns video_url or None."""
    url = f"{ARK_API_BASE}{ARK_TASKS_ENDPOINT}/{task_id}"
    elapsed = 0
    while elapsed < MAX_POLL_TIME:
        resp = await client.get(url, headers=_ark_headers())
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        logger.info("[seedance] Task %s status: %s", task_id, status)
        if status == "succeeded":
            video_url = data.get("content", {}).get("video_url")
            return video_url
        if status in ("running", "preparing", "queued"):
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            continue
        error_msg = data.get("error", {}).get("message", f"status={status}")
        logger.error("[seedance] Task %s failed: %s", task_id, error_msg)
        return None
    logger.error("[seedance] Task %s timed out after %ds", task_id, MAX_POLL_TIME)
    return None


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

async def download_video(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        logger.info("Downloaded -> %s", dest)
        return True
    except Exception as e:
        logger.error("Download failed %s: %s", url, e)
        return False


# ---------------------------------------------------------------------------
# Extract video URL from Kling result
# ---------------------------------------------------------------------------

def extract_kling_video_url(task_result: dict) -> str | None:
    """Kling task_result can be a dict or list of works with videos."""
    if isinstance(task_result, dict):
        # Direct url
        if "url" in task_result:
            return task_result["url"]
        # Nested videos list
        videos = task_result.get("videos", [])
        if videos:
            return videos[0].get("url")
    if isinstance(task_result, list):
        for work in task_result:
            videos = work.get("videos", [])
            if videos:
                return videos[0].get("url")
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_one_testcase(
    client: httpx.AsyncClient,
    tc: dict,
    results: list[dict],
):
    tc_id = tc["testcase_id"]
    prompt = tc["final_video_prompt"]
    duration = tc.get("duration_seconds", 10)
    logger.info("=== Processing %s ===", tc_id)

    # Submit to both providers concurrently
    kling_task_id, seedance_task_id = await asyncio.gather(
        kling_submit(client, prompt, duration),
        seedance_submit(client, prompt, duration),
    )

    # Poll both concurrently
    kling_result, seedance_url = await asyncio.gather(
        kling_poll(client, kling_task_id),
        seedance_poll(client, seedance_task_id),
    )

    record = {
        "testcase_id": tc_id,
        "prompt": prompt,
        "kling_task_id": kling_task_id,
        "seedance_task_id": seedance_task_id,
        "kling_video": None,
        "seedance_video": None,
    }

    # Download Kling video
    if kling_result:
        kling_url = extract_kling_video_url(kling_result)
        if kling_url:
            dest = OUTPUT_DIR / f"{tc_id}_kling.mp4"
            if await download_video(client, kling_url, dest):
                record["kling_video"] = str(dest)
                record["kling_video_url"] = kling_url

    # Download Seedance video
    if seedance_url:
        dest = OUTPUT_DIR / f"{tc_id}_seedance.mp4"
        if await download_video(client, seedance_url, dest):
            record["seedance_video"] = str(dest)
            record["seedance_video_url"] = seedance_url

    results.append(record)
    logger.info("=== Done %s | kling=%s seedance=%s ===",
                tc_id,
                "OK" if record["kling_video"] else "FAIL",
                "OK" if record["seedance_video"] else "FAIL")


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(TESTCASES_PATH) as f:
        testcases = json.load(f)

    logger.info("Loaded %d testcases", len(testcases))

    results: list[dict] = []

    # Use a single client with generous timeout for downloads
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Run all 5 testcases concurrently (10 API calls total)
        await asyncio.gather(*(
            run_one_testcase(client, tc, results)
            for tc in testcases
        ))

    # Save results summary
    summary_path = OUTPUT_DIR / "generation_results.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    logger.info("Results saved to %s", summary_path)

    # Print summary
    print("\n" + "=" * 60)
    print("GENERATION SUMMARY")
    print("=" * 60)
    for r in results:
        print(f"\n{r['testcase_id']}:")
        print(f"  Kling:    {'OK -> ' + r['kling_video'] if r['kling_video'] else 'FAILED'}")
        print(f"  Seedance: {'OK -> ' + r['seedance_video'] if r['seedance_video'] else 'FAILED'}")


if __name__ == "__main__":
    asyncio.run(main())
