"""
replay_tracker.py — Log pipeline results to the WordPress dashboard and/or Google Sheets.

Primary backend (recommended): install the Ganjier Replay Pipeline WordPress plugin and
log via REST API — visible under Tools → Replay Pipeline in wp-admin.

Optional secondary backend: Google Sheets migration tracker on Shared Drive.

Env:
    REPLAY_TRACKER_BACKEND=wordpress|sheets|both   # default: wordpress
    WP_BASE_URL / WP_USER / WP_APP_PASSWORD        # used for WordPress tracker
    GOOGLE_SHEETS_SPREADSHEET_ID                   # optional Sheets mirror

CLI:
    python replay_tracker.py --test
    python replay_tracker.py --headers
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

logger = logging.getLogger("replay_tracker")

WP_PIPELINE_RUNS_PATH = "/wp-json/gg/v1/pipeline-runs"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DEFAULT_HEADERS = [
    "Topic",
    "Recording Date",
    "Duration (min)",
    "Zoom Account",
    "YouTube URL",
    "WordPress URL",
    "Status",
    "Error",
    "Processed At",
    "Recording ID",
    "MEC Event URL",
]

# Map logical field → acceptable header labels (lowercase).
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "topic": ("topic", "title", "session", "session title", "meeting topic", "name"),
    "date": ("recording date", "date", "meeting date", "session date"),
    "duration": ("duration (min)", "duration", "length", "duration min"),
    "account": ("zoom account", "account", "zoom"),
    "youtube_url": ("youtube url", "youtube", "youtube link", "yt url", "youtube url link"),
    "wp_url": ("wordpress url", "wp url", "replay url", "post url", "wordpress", "wp link"),
    "status": ("status", "pipeline status", "processing status"),
    "error": ("error", "error message", "notes", "failure", "failure reason"),
    "processed_at": ("processed at", "processed", "updated", "last updated", "timestamp"),
    "recording_id": ("recording id", "zoom recording id", "id", "meeting id"),
    "mec_event_url": ("mec event url", "calendar event url", "mec url", "event url"),
}

_FIELD_TO_HEADER: dict[str, str] = {
    "topic": "Topic",
    "date": "Recording Date",
    "duration": "Duration (min)",
    "account": "Zoom Account",
    "youtube_url": "YouTube URL",
    "wp_url": "WordPress URL",
    "status": "Status",
    "error": "Error",
    "processed_at": "Processed At",
    "recording_id": "Recording ID",
    "mec_event_url": "MEC Event URL",
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def spreadsheet_id_from_gsheet_file(path: str | Path) -> str | None:
    """
    Extract a spreadsheet ID from a Google Drive Desktop ``.gsheet`` shortcut file.

    Example path (Windows):
        h:\\Shared drives\\...\\Ganjier Guild Replay Library Tracker.gsheet
    """
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    doc_id = data.get("doc_id") or data.get("resource_id", "").replace("spreadsheet:", "")
    if doc_id:
        return str(doc_id)

    url = data.get("url", "")
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None


def tracker_backend() -> str:
    """Return tracker backend mode: wordpress, sheets, or both."""
    value = os.getenv("REPLAY_TRACKER_BACKEND", "wordpress").strip().lower()
    if value in {"wordpress", "sheets", "both"}:
        return value
    return "wordpress"


def wp_is_configured() -> bool:
    return bool(
        os.getenv("WP_BASE_URL", "").strip()
        and os.getenv("WP_USER", "").strip()
        and os.getenv("WP_APP_PASSWORD", "").strip()
    )


def sheets_is_configured() -> bool:
    return bool(resolve_spreadsheet_id() and _service_account_credentials() is not None)


def resolve_spreadsheet_id() -> str:
    """Return spreadsheet ID from env or an optional .gsheet shortcut path."""
    explicit = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "").strip()
    if explicit:
        return explicit

    shortcut = os.getenv("GOOGLE_SHEETS_SHORTCUT_PATH", "").strip()
    if shortcut:
        found = spreadsheet_id_from_gsheet_file(shortcut)
        if found:
            return found

    return ""


def is_configured() -> bool:
    """True when at least one tracker backend is available."""
    backend = tracker_backend()
    if backend == "sheets":
        return sheets_is_configured()
    if backend == "both":
        return wp_is_configured() or sheets_is_configured()
    return wp_is_configured()


def _service_account_credentials():
    from google.oauth2 import service_account

    json_blob = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if json_blob:
        info = json.loads(json_blob)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if not file_path:
        default = SCRIPT_DIR / "service_account.json"
        if default.exists():
            file_path = str(default)
    if file_path and Path(file_path).exists():
        return service_account.Credentials.from_service_account_from_file(file_path, scopes=SCOPES)

    return None


def _sheets_service():
    from googleapiclient.discovery import build

    creds = _service_account_credentials()
    if creds is None:
        raise RuntimeError(
            "Google Sheets tracker not configured — set GOOGLE_SERVICE_ACCOUNT_JSON "
            "or GOOGLE_SERVICE_ACCOUNT_FILE in .env"
        )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def worksheet_name() -> str:
    return os.getenv("GOOGLE_SHEETS_WORKSHEET_NAME", "").strip()


def _sheet_range(suffix: str = "A1:Z1") -> str:
    tab = worksheet_name()
    if tab:
        escaped = tab.replace("'", "''")
        return f"'{escaped}'!{suffix}"
    return suffix


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def resolve_column_map(headers: list[str]) -> dict[str, int]:
    """Map logical field names to zero-based column indices from row-1 headers."""
    normalized = {_normalize_header(h): idx for idx, h in enumerate(headers) if h.strip()}
    column_map: dict[str, int] = {}

    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                column_map[field] = normalized[alias]
                break

    return column_map


def read_headers() -> list[str]:
    service = _sheets_service()
    spreadsheet_id = resolve_spreadsheet_id()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=_sheet_range("1:1"))
        .execute()
    )
    row = result.get("values", [[]])
    return row[0] if row else []


def ensure_headers() -> list[str]:
    """Write default headers when row 1 is empty and GOOGLE_SHEETS_ENSURE_HEADERS is set."""
    headers = read_headers()
    if headers and any(cell.strip() for cell in headers):
        return headers

    if not _env_bool("GOOGLE_SHEETS_ENSURE_HEADERS", default=True):
        return headers

    service = _sheets_service()
    spreadsheet_id = resolve_spreadsheet_id()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=_sheet_range("1:1"),
        valueInputOption="RAW",
        body={"values": [DEFAULT_HEADERS]},
    ).execute()
    logger.info("Wrote default tracker headers to row 1.")
    return DEFAULT_HEADERS


def _row_from_record(headers: list[str], column_map: dict[str, int], record: dict[str, Any]) -> list[str]:
    width = max(len(headers), max(column_map.values(), default=-1) + 1, len(DEFAULT_HEADERS))
    row = [""] * width

    for field, value in record.items():
        if value is None:
            continue
        text = str(value)
        if field in column_map:
            row[column_map[field]] = text
            continue
        # Fall back to exact default header match when aliases were not found.
        header = _FIELD_TO_HEADER.get(field)
        if header and header in headers:
            row[headers.index(header)] = text

    return row


def _wp_auth_headers() -> tuple[dict[str, str], str, str]:
    wp_user = os.getenv("WP_USER", "")
    wp_password = os.getenv("WP_APP_PASSWORD", "").replace(" ", "")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return headers, wp_user, wp_password


def log_to_wordpress(record: dict[str, Any]) -> None:
    """Upsert a pipeline run via the Ganjier Replay Pipeline WordPress plugin."""
    if not wp_is_configured():
        logger.debug("WordPress tracker not configured; skipping.")
        return

    base_url = os.getenv("WP_BASE_URL", "").rstrip("/")
    headers, wp_user, wp_password = _wp_auth_headers()
    payload = {
        "recording_id": record.get("recording_id", ""),
        "topic": record.get("topic", ""),
        "recording_date": record.get("date", ""),
        "duration_min": int(record.get("duration", 0) or 0),
        "zoom_account": record.get("account", ""),
        "youtube_url": record.get("youtube_url", ""),
        "wp_url": record.get("wp_url", ""),
        "status": record.get("status", ""),
        "error": record.get("error", ""),
        "processed_at": record.get("processed_at", ""),
        "mec_event_url": record.get("mec_event_url", ""),
    }

    response = requests.post(
        f"{base_url}{WP_PIPELINE_RUNS_PATH}",
        headers=headers,
        json=payload,
        auth=(wp_user, wp_password),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"WordPress tracker HTTP {response.status_code}: {response.text[:500]}"
        )
    logger.info(
        "Logged pipeline run to WordPress tracker for topic=%r status=%s",
        record.get("topic"),
        record.get("status"),
    )


def append_row(record: dict[str, Any]) -> None:
    """Append one tracker row, matching columns by header aliases in row 1."""
    if not sheets_is_configured():
        logger.debug("Sheets tracker not configured; skipping append.")
        return

    headers = ensure_headers()
    column_map = resolve_column_map(headers)
    if not column_map:
        logger.warning(
            "Tracker sheet row 1 has no recognizable headers. "
            "Expected labels like Topic, Recording Date, YouTube URL, Status."
        )
        column_map = {field: idx for idx, field in enumerate(_FIELD_TO_HEADER)}

    row = _row_from_record(headers, column_map, record)
    service = _sheets_service()
    spreadsheet_id = resolve_spreadsheet_id()
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=_sheet_range("A:A"),
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
    logger.info("Appended tracker row for topic=%r status=%s", record.get("topic"), record.get("status"))


def _build_record(
    *,
    topic: str,
    recording_date: datetime,
    duration_minutes: int,
    status: str,
    zoom_account: str | None = None,
    youtube_url: str | None = None,
    wp_url: str | None = None,
    error: str | None = None,
    recording_id: str | None = None,
    mec_event_url: str | None = None,
) -> dict[str, Any]:
    return {
        "topic": topic,
        "date": recording_date.strftime("%Y-%m-%d"),
        "duration": duration_minutes,
        "account": zoom_account or "",
        "youtube_url": youtube_url or "",
        "wp_url": wp_url or "",
        "status": status,
        "error": error or "",
        "processed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "recording_id": recording_id or "",
        "mec_event_url": mec_event_url or "",
    }


def log_pipeline_result(
    *,
    topic: str,
    recording_date: datetime,
    duration_minutes: int,
    status: str,
    zoom_account: str | None = None,
    youtube_url: str | None = None,
    wp_url: str | None = None,
    error: str | None = None,
    recording_id: str | None = None,
    mec_event_url: str | None = None,
) -> None:
    """Best-effort tracker update; never raises to the caller."""
    if not is_configured():
        return

    record = _build_record(
        topic=topic,
        recording_date=recording_date,
        duration_minutes=duration_minutes,
        status=status,
        zoom_account=zoom_account,
        youtube_url=youtube_url,
        wp_url=wp_url,
        error=error,
        recording_id=recording_id,
        mec_event_url=mec_event_url,
    )
    backend = tracker_backend()

    if backend in {"wordpress", "both"}:
        try:
            log_to_wordpress(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to update WordPress replay tracker: %s", exc)

    if backend in {"sheets", "both"}:
        try:
            append_row(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to update Google Sheets replay tracker: %s", exc)


def account_label_from_payload(payload: dict) -> str:
    """Map Zoom account_id in a webhook payload to a friendly account name."""
    account_id = payload.get("payload", {}).get("account_id", "")
    mapping = {
        os.getenv("ZOOM_JWARD_ACCOUNT_ID", ""): "jward",
        os.getenv("ZOOM_NAVIGATORS_ACCOUNT_ID", ""): "navigators",
    }
    return mapping.get(account_id, account_id or "unknown")


def recording_id_from_payload(payload: dict) -> str:
    obj = payload.get("payload", {}).get("object", {})
    meeting_id = obj.get("id") or obj.get("uuid") or ""
    start_time = obj.get("start_time", "")
    return f"{meeting_id}:{start_time}" if meeting_id else start_time


def _cmd_test() -> int:
    backend = tracker_backend()
    print(f"Backend mode: {backend}")
    ok = True

    if backend in {"wordpress", "both"}:
        if not wp_is_configured():
            print("FAIL  WordPress tracker: WP_BASE_URL / WP_USER / WP_APP_PASSWORD missing")
            ok = False
        else:
            base_url = os.getenv("WP_BASE_URL", "").rstrip("/")
            try:
                response = requests.get(
                    f"{base_url}{WP_PIPELINE_RUNS_PATH}",
                    auth=(
                        os.getenv("WP_USER", ""),
                        os.getenv("WP_APP_PASSWORD", "").replace(" ", ""),
                    ),
                    timeout=15,
                )
                if response.status_code == 404:
                    print(
                        "FAIL  WordPress tracker endpoint not found — install/activate "
                        "wordpress-plugin/ganjier-replay-pipeline on the site"
                    )
                    ok = False
                elif response.status_code == 401:
                    print("FAIL  WordPress tracker auth failed — check WP_USER / WP_APP_PASSWORD")
                    ok = False
                elif response.ok:
                    count = response.json().get("count", 0)
                    print(f"OK    WordPress tracker reachable ({count} recent runs)")
                else:
                    print(f"FAIL  WordPress tracker HTTP {response.status_code}")
                    ok = False
            except requests.RequestException as exc:
                print(f"FAIL  WordPress tracker unreachable: {exc}")
                ok = False

    if backend in {"sheets", "both"}:
        if not sheets_is_configured():
            print("FAIL  Google Sheets tracker not configured")
            ok = False
        else:
            headers = read_headers()
            print(f"OK    Spreadsheet ID: {resolve_spreadsheet_id()}")
            print(f"OK    Worksheet: {worksheet_name() or '(first tab)'}")
            print(f"OK    Headers ({len(headers)}): {', '.join(headers) if headers else '(empty)'}")
            print(f"OK    Column map: {resolve_column_map(headers)}")

    return 0 if ok else 1


def _cmd_headers() -> int:
    for header in read_headers() if is_configured() else DEFAULT_HEADERS:
        print(header)
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Ganjier Guild replay tracker (WordPress + optional Sheets)")
    parser.add_argument("--test", action="store_true", help="Verify Sheets API access and headers")
    parser.add_argument("--headers", action="store_true", help="Print row-1 headers")
    args = parser.parse_args()

    if args.test:
        raise SystemExit(_cmd_test())
    if args.headers:
        raise SystemExit(_cmd_headers())

    parser.print_help()


if __name__ == "__main__":
    main()
