"""Re-run only Seedance with correct duration (12s) for all 5 testcases."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ARK_API_KEY = os.environ["ARK_API_KEY"]
ARK_API_BASE = "https://ark.cn-beijing.volces.com"
ARK_TASKS_ENDPOINT = "/api/v3/contents/generations/tasks"
SEEDANCE_MODEL = "doubao-seedance-2-0-260128"

OUTPUT_DIR = Path(__file__).parent / "outputs" / "videos"
TESTCASES_PATH = Path(__file__).parent / "outputs" / "compiled_testcases.json"
POLL_INTERVAL = 15
MAX_POLL_TIME = 600


def _ark_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {ARK_API_KEY}",
        "Content-Type": "application/json",
    }


async def seedance_submit(client: httpx.AsyncClient, prompt: str, duration: int) -> str:
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
    logger.info("[seedance] Submitted task %s (duration=%ds)", task_id, duration)
    return task_id


async def seedance_poll(client: httpx.AsyncClient, task_id: str) -> str | None:
    url = f"{ARK_API_BASE}{ARK_TASKS_ENDPOINT}/{task_id}"
    elapsed = 0
    while elapsed < MAX_POLL_TIME:
        resp = await client.get(url, headers=_ark_headers())
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        logger.info("[seedance] Task %s status: %s", task_id, status)
        if status == "succeeded":
            return data.get("content", {}).get("video_url")
        if status in ("running", "preparing", "queued"):
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            continue
        error_msg = data.get("error", {}).get("message", f"status={status}")
        logger.error("[seedance] Task %s failed: %s", task_id, error_msg)
        return None
    logger.error("[seedance] Task %s timed out", task_id)
    return None


async def download_video(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        logger.info("Downloaded -> %s", dest)
        return True
    except Exception as e:
        logger.error("Download failed: %s", e)
        return False


async def run_one(client: httpx.AsyncClient, tc: dict) -> dict:
    tc_id = tc["testcase_id"]
    prompt = tc["final_video_prompt"]
    duration = tc.get("duration_seconds", 10)

    task_id = await seedance_submit(client, prompt, duration)
    video_url = await seedance_poll(client, task_id)

    result = {"testcase_id": tc_id, "task_id": task_id, "duration": duration, "video": None}
    if video_url:
        dest = OUTPUT_DIR / f"{tc_id}_seedance.mp4"
        if await download_video(client, video_url, dest):
            result["video"] = str(dest)
            result["video_url"] = video_url

    status = "OK" if result["video"] else "FAIL"
    logger.info("=== %s seedance %s ===", tc_id, status)
    return result


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(TESTCASES_PATH) as f:
        testcases = json.load(f)

    logger.info("Re-running Seedance for %d testcases with correct duration", len(testcases))

    async with httpx.AsyncClient(timeout=120.0) as client:
        results = await asyncio.gather(*(run_one(client, tc) for tc in testcases))

    print("\n" + "=" * 60)
    print("SEEDANCE RE-RUN SUMMARY")
    print("=" * 60)
    for r in results:
        print(f"\n{r['testcase_id']} (duration={r['duration']}s):")
        print(f"  {'OK -> ' + r['video'] if r['video'] else 'FAILED'}")


if __name__ == "__main__":
    asyncio.run(main())
