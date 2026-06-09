"""
poll_zoom.py — Fallback poller for missed Zoom recording.completed webhooks.

Run every 30 minutes via cron when webhook delivery is unreliable:

    */30 * * * * /usr/bin/python3 /path/to/poll_zoom.py >> /var/log/zoom-poll.log 2>&1
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from backfill import build_pipeline_payload, filter_recordings
from zoom_auth import ZoomAuth, configured_accounts, zoom_api_get

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

LOOKBACK_HOURS = int(os.getenv("POLL_LOOKBACK_HOURS", "48"))
STATE_FILE = SCRIPT_DIR / "logs" / "processed_ids.json"

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


def load_processed_ids() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("processed", []))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s (%s) — starting fresh.", STATE_FILE, exc)
        return set()


def save_processed_ids(processed: set[str]) -> None:
    tmp_path = STATE_FILE.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump({"processed": sorted(processed)}, fh, indent=2)
    tmp_path.replace(STATE_FILE)


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

    processed = load_processed_ids()
    new_count = 0

    for auth in accounts:
        logger.info("[%s] Polling recordings from the last %d hours …", auth.name, LOOKBACK_HOURS)
        meetings = fetch_recent_recordings(auth)
        recordings = filter_recordings(meetings)

        for rec in recordings:
            key = f"{auth.name}:{rec['uuid']}"
            if key in processed:
                continue

            logger.info("[%s] Processing missed recording: %s", auth.name, rec["topic"])
            payload = build_pipeline_payload(rec, auth.account_id)
            try:
                run_pipeline(payload)
            except SystemExit as exc:
                logger.error("[%s] Pipeline failed for %s (exit %s)", auth.name, key, exc.code)
                continue

            processed.add(key)
            save_processed_ids(processed)
            new_count += 1
            time.sleep(float(os.getenv("BACKFILL_DELAY_SECONDS", "5")))

    logger.info("Poll complete — %d new recording(s) processed.", new_count)


if __name__ == "__main__":
    main()
