"""
poll_zoom.py — Fallback poller for missed Zoom recording.completed webhooks.

Run every 30 minutes via cron when webhook delivery is unreliable:

    */30 * * * * /usr/bin/python3 /path/to/poll_zoom.py >> /var/log/zoom-poll.log 2>&1
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from backfill import build_pipeline_payload, filter_recordings
from processing_state import load_processed_keys, mark_processed, recording_key
from zoom_auth import ZoomAuth, auth_status, configured_accounts, zoom_api_get

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

LOOKBACK_HOURS = int(os.getenv("POLL_LOOKBACK_HOURS", "48"))

LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "poll_zoom.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("poll_zoom")


def fetch_recent_recordings(auth: ZoomAuth) -> list[dict]:
    from_date = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%d")
    to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    meetings: list[dict] = []
    next_page_token = ""

    while True:
        params: dict = {
            "from": from_date,
            "to": to_date,
            "page_size": 300,
        }
        if next_page_token:
            params["next_page_token"] = next_page_token

        data = zoom_api_get("/v2/users/me/recordings", auth, params)
        meetings.extend(data.get("meetings", []))
        next_page_token = data.get("next_page_token", "")
        if not next_page_token:
            break

    return meetings


def main() -> None:
    accounts = configured_accounts()
    if not accounts:
        logger.error("No Zoom accounts configured in .env")
        sys.exit(1)

    sys.path.insert(0, str(SCRIPT_DIR))
    from pipeline import run_pipeline  # noqa: WPS433

    processed = load_processed_keys()
    new_count = 0

    for auth in accounts:
        ok, message = auth_status(auth)
        if not ok:
            logger.error("[%s] Zoom OAuth failed — skipping account. %s", auth.name, message)
            continue

        logger.info("[%s] Polling recordings from the last %d hours …", auth.name, LOOKBACK_HOURS)
        try:
            meetings = fetch_recent_recordings(auth)
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Could not list recordings — skipping: %s", auth.name, exc)
            continue
        recordings = filter_recordings(meetings)

        for rec in recordings:
            key = recording_key(auth.name, rec["uuid"])
            if key in processed:
                continue

            logger.info("[%s] Processing missed recording: %s", auth.name, rec["topic"])
            payload = build_pipeline_payload(rec, auth.account_id)
            try:
                run_pipeline(payload)
            except SystemExit as exc:
                logger.error("[%s] Pipeline failed for %s (exit %s)", auth.name, key, exc.code)
                continue

            mark_processed(key)
            processed.add(key)
            new_count += 1
            time.sleep(float(os.getenv("BACKFILL_DELAY_SECONDS", "5")))

    logger.info("Poll complete — %d new recording(s) processed.", new_count)


if __name__ == "__main__":
    main()
