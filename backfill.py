"""
Zoom Cloud Recording Backfill Scanner
======================================
Scans ALL Zoom cloud recordings and runs them through the existing pipeline.

Usage:
    python backfill.py                  # run full backfill (both accounts)
    python backfill.py --account jward  # run only the jward account
    python backfill.py --dry-run        # preview recordings without processing
    python backfill.py --reset-state    # clear state file and start fresh
    python backfill.py --retry-failed   # only retry previously failed recordings
    python backfill.py --yesterday      # only yesterday's recordings (CT timezone)
    python backfill.py --from-date 2026-06-08 --to-date 2026-06-08
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from processing_state import load_processed_keys, mark_processed, recording_key
from zoom_auth import ZoomAuth, auth_status, configured_accounts, zoom_api_get

# ---------------------------------------------------------------------------
# Load environment variables from .env in the same directory as this script
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

BACKFILL_FROM_DATE   = os.getenv("BACKFILL_FROM_DATE", "2020-01-01")
BACKFILL_DELAY_SEC   = float(os.getenv("BACKFILL_DELAY_SECONDS", "5"))
BACKFILL_TOPIC_FILTER = os.getenv("BACKFILL_TOPIC_FILTER", "").strip().lower()
BACKFILL_TIMEZONE    = os.getenv("BACKFILL_TIMEZONE", "America/Chicago")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_DIR  = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "backfill.log"

_fmt = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
_file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_fmt)

logger = logging.getLogger("zoom_backfill")
logger.setLevel(logging.DEBUG)
logger.addHandler(_file_handler)
logger.addHandler(_stream_handler)

# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------
STATE_FILE = LOG_DIR / "backfill_state.json"

_STATE_TEMPLATE: dict = {"processed": [], "failed": {}}


def load_state() -> dict:
    """Load backfill state from disk. Returns empty state if file does not exist."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as fh:
                state = json.load(fh)
            # Ensure expected keys exist
            state.setdefault("processed", [])
            state.setdefault("failed", {})
            return state
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state file (%s) — starting fresh.", exc)
    return {"processed": [], "failed": {}}


def save_state(state: dict) -> None:
    """Persist backfill state to disk atomically."""
    tmp_path = STATE_FILE.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    tmp_path.replace(STATE_FILE)


def _local_start_time_window(
    from_date: str | None = None,
    to_date: str | None = None,
    yesterday_only: bool = False,
) -> tuple[datetime, datetime] | None:
    """Return a local start-time window for single-day backfills."""
    if yesterday_only:
        tz = ZoneInfo(BACKFILL_TIMEZONE)
        start = (datetime.now(tz) - timedelta(days=1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return start, start + timedelta(days=1)

    if not from_date or not to_date or from_date.strip() != to_date.strip():
        return None

    try:
        day = date.fromisoformat(from_date.strip())
    except ValueError:
        return None

    tz = ZoneInfo(BACKFILL_TIMEZONE)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start, start + timedelta(days=1)


def resolve_date_range(
    from_date: str | None = None,
    to_date: str | None = None,
    yesterday_only: bool = False,
) -> tuple[str, str]:
    """
    Resolve the Zoom List Recordings date window.

    Defaults to BACKFILL_FROM_DATE through today (UTC). CLI flags override .env.
    Single-day local windows are expanded to the UTC dates Zoom expects.
    """
    local_window = _local_start_time_window(from_date, to_date, yesterday_only)
    if local_window:
        start, end = local_window
        return (
            start.astimezone(timezone.utc).date().isoformat(),
            end.astimezone(timezone.utc).date().isoformat(),
        )

    resolved_from = (from_date or BACKFILL_FROM_DATE).strip()
    resolved_to = (to_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    return resolved_from, resolved_to


def _parse_zoom_datetime(value: str) -> datetime | None:
    """Parse Zoom ISO timestamps, treating naive values as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def reset_state() -> None:
    """Delete the state file so the next run starts completely fresh."""
    if STATE_FILE.exists():
        STATE_FILE.unlink()
        logger.info("State file deleted — starting fresh on next run.")
    else:
        logger.info("No state file found; nothing to reset.")


# ---------------------------------------------------------------------------
# Fetch all recordings
# ---------------------------------------------------------------------------

def fetch_all_recordings(
    auth: ZoomAuth,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """
    Retrieve all Zoom cloud recordings for the given account.

    Paginates through all pages using next_page_token.

    Parameters
    ----------
    auth : ZoomAuth
        ZoomAuth instance for the account to fetch recordings from.
    from_date : str
        Start date (YYYY-MM-DD) for the Zoom List Recordings query.
    to_date : str
        End date (YYYY-MM-DD) for the Zoom List Recordings query.

    Returns a list of meeting objects (from the Zoom List Recordings response).
    Each object contains: uuid, topic, start_time, duration, recording_files, etc.
    """
    page_size  = 300  # Zoom's max

    logger.info("[%s] Fetching recordings from %s to %s …", auth.name, from_date, to_date)

    meetings: list[dict] = []
    next_page_token = ""
    page_num = 0

    while True:
        page_num += 1
        params: dict = {
            "from":      from_date,
            "to":        to_date,
            "page_size": page_size,
        }
        if next_page_token:
            params["next_page_token"] = next_page_token

        data = zoom_api_get("/v2/users/me/recordings", auth, params)

        page_meetings = data.get("meetings", [])
        meetings.extend(page_meetings)
        logger.debug("[%s] Page %d: fetched %d meetings (total so far: %d).", auth.name, page_num, len(page_meetings), len(meetings))

        next_page_token = data.get("next_page_token", "")
        if not next_page_token:
            break

    logger.info("[%s] Total meetings fetched: %d", auth.name, len(meetings))
    return meetings


# ---------------------------------------------------------------------------
# Filter and normalise recordings
# ---------------------------------------------------------------------------

def filter_recordings(
    meetings: list[dict],
    start_time_window: tuple[datetime, datetime] | None = None,
) -> list[dict]:
    """
    Filter meetings to only those with at least one completed MP4 recording file.
    Applies optional BACKFILL_TOPIC_FILTER (case-insensitive substring match)
    and optional local start-time bounds.

    Returns a list of dicts, one per qualifying meeting, each with the best/first
    completed MP4 recording file identified.
    """
    results = []

    for meeting in meetings:
        topic = meeting.get("topic", "")

        if start_time_window:
            start_time = _parse_zoom_datetime(meeting.get("start_time", ""))
            if start_time is None or not (
                start_time_window[0] <= start_time < start_time_window[1]
            ):
                continue

        # Topic filter (optional)
        if BACKFILL_TOPIC_FILTER and BACKFILL_TOPIC_FILTER not in topic.lower():
            continue

        # Find completed MP4 files
        rec_files = meeting.get("recording_files", [])
        mp4_files = [
            f for f in rec_files
            if f.get("file_type", "").upper() == "MP4"
            and f.get("status", "").lower() == "completed"
        ]

        if not mp4_files:
            continue

        # Use the first qualifying MP4
        results.append({
            "uuid":       meeting.get("uuid", ""),
            "topic":      topic,
            "start_time": meeting.get("start_time", ""),
            "duration":   meeting.get("duration", 0),
            "mp4_file":   mp4_files[0],
            "all_files":  mp4_files,
        })

    logger.info("Recordings after filtering: %d", len(results))
    return results


# ---------------------------------------------------------------------------
# Build pipeline payload
# ---------------------------------------------------------------------------

def build_pipeline_payload(recording: dict, account_id: str) -> dict:
    """
    Construct a Zoom recording.completed webhook payload dict
    from a normalised recording dict, matching the shape expected by run_pipeline().
    """
    mp4 = recording["mp4_file"]
    return {
        "event": "recording.completed",
        "payload": {
            "account_id": account_id,
            "object": {
                "uuid":      recording["uuid"],
                "topic":     recording["topic"],
                "start_time": recording["start_time"],
                "duration":  recording["duration"],
                "recording_files": [
                    {
                        "file_type":    "MP4",
                        "download_url": mp4.get("download_url", ""),
                        "status":       "completed",
                    }
                ],
            }
        },
    }


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def run_backfill(
    dry_run: bool = False,
    retry_failed: bool = False,
    account_filter: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    yesterday_only: bool = False,
) -> None:
    """
    Core backfill loop. Iterates over all configured Zoom accounts (or a single
    account if account_filter is provided).

    Parameters
    ----------
    dry_run : bool
        If True, list recordings without running the pipeline.
    retry_failed : bool
        If True, only process recordings previously marked as failed.
    account_filter : str or None
        If provided, only run the account with this name. Default runs all accounts.
    from_date : str or None
        Optional override for BACKFILL_FROM_DATE (YYYY-MM-DD).
    to_date : str or None
        Optional override for the end date (YYYY-MM-DD). Defaults to today (UTC).
    yesterday_only : bool
        If True, only fetch recordings from yesterday in BACKFILL_TIMEZONE.
    """
    start_time = time.monotonic()
    range_from, range_to = resolve_date_range(from_date, to_date, yesterday_only)
    start_time_window = _local_start_time_window(from_date, to_date, yesterday_only)
    logger.info("Backfill date range: %s to %s", range_from, range_to)

    # Determine which accounts to run (preserves per-account ACCESS_TOKEN fallbacks)
    accounts_to_run = configured_accounts()
    if account_filter:
        accounts_to_run = [auth for auth in accounts_to_run if auth.name == account_filter]
        if not accounts_to_run:
            logger.error(
                "--account '%s' not found or missing credentials. Available: %s",
                account_filter,
                [auth.name for auth in configured_accounts()],
            )
            sys.exit(1)

    if not accounts_to_run:
        logger.error(
            "No Zoom accounts configured. Set ZOOM_JWARD_* or ZOOM_NAVIGATORS_* in .env"
        )
        sys.exit(1)

    # Load existing state
    state = load_state()
    processed_set = load_processed_keys()
    failed_map    = state["failed"]

    total_found_all    = 0
    total_processed_all = 0
    total_skipped_all   = 0
    total_failed_all    = 0

    # Import run_pipeline once (skip in dry-run)
    if not dry_run:
        try:
            sys.path.insert(0, str(SCRIPT_DIR))
            from pipeline import run_pipeline  # type: ignore
        except ImportError as exc:
            logger.error("Could not import run_pipeline from pipeline.py: %s", exc)
            sys.exit(1)

    for auth in accounts_to_run:
        ok, message = auth_status(auth)
        if not ok:
            logger.error("[%s] Zoom OAuth failed — skipping account. %s", auth.name, message)
            continue

        # Fetch + filter for this account
        try:
            raw_meetings = fetch_all_recordings(auth, range_from, range_to)
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Could not list recordings — skipping: %s", auth.name, exc)
            continue
        recordings   = filter_recordings(raw_meetings, start_time_window)
        total_found  = len(recordings)
        total_found_all += total_found

        if total_found == 0:
            logger.info("[%s] No qualifying recordings found.", auth.name)
            continue

        # State keys are "{account_name}:{meeting_uuid}" to avoid cross-account collisions
        def _state_key(uuid: str) -> str:
            return recording_key(auth.name, uuid)

        # Determine which recordings to process
        if retry_failed:
            to_process = [r for r in recordings if _state_key(r["uuid"]) in failed_map]
            logger.info(
                "[%s] --retry-failed: %d previously failed recording(s) will be retried.",
                auth.name, len(to_process),
            )
        else:
            to_process = recordings

        total_to_run = len(to_process)

        # Dry-run mode: just list
        if dry_run:
            print(f"\n{'─'*60}")
            print(f"  DRY RUN [{auth.name}] — {total_found} recording(s) found, {total_to_run} to process\n")
            for idx, rec in enumerate(to_process, 1):
                uuid           = rec["uuid"]
                topic          = rec["topic"]
                start_time_str = rec["start_time"][:10] if rec["start_time"] else "unknown"
                duration       = rec["duration"]
                already        = "  [ALREADY DONE]" if _state_key(uuid) in processed_set and not retry_failed else ""
                failed_tag     = "  [PREV FAILED]" if _state_key(uuid) in failed_map else ""
                print(
                    f"  [{idx:>3}/{total_to_run}] {topic} — {start_time_str}"
                    f" ({duration} min){already}{failed_tag}"
                )
            print(f"\n{'─'*60}\n")
            continue

        run_index      = 0
        total_skipped  = 0
        total_processed = 0
        total_failed   = 0

        for rec in to_process:
            uuid       = rec["uuid"]
            topic      = rec["topic"]
            start_date = rec["start_time"][:10] if rec["start_time"] else "unknown"
            key        = _state_key(uuid)

            # Skip already processed (unless retrying failed)
            if not retry_failed and key in processed_set:
                total_skipped += 1
                continue

            # If retrying failed, remove from failed_map first
            if retry_failed and key in failed_map:
                del failed_map[key]
                state["failed"] = failed_map

            run_index += 1
            label = f"[{auth.name} {run_index}/{total_to_run}] {topic} — {start_date}"

            logger.info("Processing: %s (uuid=%s)", label, uuid)

            payload = build_pipeline_payload(rec, auth.account_id)
            try:
                run_pipeline(payload)
                # Success
                if key not in state["processed"]:
                    state["processed"].append(key)
                processed_set.add(key)
                mark_processed(key)
                # Remove from failed if it was there
                state["failed"].pop(key, None)
                save_state(state)
                total_processed += 1
                print(f"  {label} \u2713")
            except SystemExit as exc:
                # pipeline.py calls sys.exit(1) on errors — capture it
                err_msg = f"pipeline exited with code {exc.code}"
                logger.error("Pipeline failed for %s: %s", key, err_msg)
                state["failed"][key] = err_msg
                save_state(state)
                total_failed += 1
                print(f"  {label} \u2717 FAILED: {err_msg}")
            except Exception as exc:  # noqa: BLE001
                err_msg = str(exc)
                logger.error("Pipeline failed for %s: %s", key, err_msg, exc_info=True)
                state["failed"][key] = err_msg
                save_state(state)
                total_failed += 1
                print(f"  {label} \u2717 FAILED: {err_msg}")

            # Rate-limiting delay between recordings
            if run_index < total_to_run:
                time.sleep(BACKFILL_DELAY_SEC)

        total_processed_all += total_processed
        total_skipped_all   += total_skipped
        total_failed_all    += total_failed

    elapsed = time.monotonic() - start_time
    _print_summary(total_found_all, total_processed_all, total_skipped_all, total_failed_all, elapsed)


def _print_summary(
    total_found: int,
    total_processed: int,
    total_skipped: int,
    total_failed: int,
    elapsed_sec: float,
) -> None:
    """Print a human-readable summary line."""
    minutes, seconds = divmod(int(elapsed_sec), 60)
    elapsed_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"

    print()
    print("=" * 60)
    print("  BACKFILL SUMMARY")
    print("=" * 60)
    print(f"  Total recordings found  : {total_found}")
    print(f"  Processed               : {total_processed}")
    print(f"  Skipped (already done)  : {total_skipped}")
    print(f"  Failed                  : {total_failed}")
    print(f"  Time elapsed            : {elapsed_str}")
    print("=" * 60)
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zoom Cloud Recording Backfill Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List all recordings that would be processed without running the pipeline.",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Delete the state file and exit (next run will start fresh).",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Only retry recordings previously marked as failed.",
    )
    parser.add_argument(
        "--account",
        metavar="NAME",
        default=None,
        help="Only run the named account (e.g. jward or navigators). Default runs all accounts.",
    )
    parser.add_argument(
        "--from-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Only fetch recordings on or after this date (overrides BACKFILL_FROM_DATE).",
    )
    parser.add_argument(
        "--to-date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Only fetch recordings on or before this date (default: today UTC).",
    )
    parser.add_argument(
        "--yesterday",
        action="store_true",
        help=(
            "Only fetch yesterday's recordings "
            f"(timezone: {BACKFILL_TIMEZONE}). Shorthand for a one-day test backfill."
        ),
    )
    args = parser.parse_args()

    if args.reset_state:
        reset_state()
        sys.exit(0)

    if args.yesterday and (args.from_date or args.to_date):
        parser.error("--yesterday cannot be combined with --from-date or --to-date")

    run_backfill(
        dry_run=args.dry_run,
        retry_failed=args.retry_failed,
        account_filter=args.account,
        from_date=args.from_date,
        to_date=args.to_date,
        yesterday_only=args.yesterday,
    )


if __name__ == "__main__":
    main()
