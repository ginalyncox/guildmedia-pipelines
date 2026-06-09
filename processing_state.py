"""Shared processed-recording state for webhook, poll, and backfill paths."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
LOG_DIR = SCRIPT_DIR / "logs"
POLL_STATE_FILE = LOG_DIR / "processed_ids.json"
BACKFILL_STATE_FILE = LOG_DIR / "backfill_state.json"

logger = logging.getLogger(__name__)
_state_lock = threading.Lock()


def recording_key(account_name: str, uuid: str) -> str:
    """Build the shared processed-state key for a Zoom recording."""
    return f"{account_name}:{uuid}"


def recording_key_from_payload(payload: dict) -> str | None:
    """Build a shared processed-state key from a Zoom webhook payload."""
    payload_data = payload.get("payload", {})
    obj = payload_data.get("object", {})
    uuid = obj.get("uuid")
    if not uuid:
        return None

    account_id = payload_data.get("account_id", "")
    account_name = account_id or "unknown"
    if account_id:
        from zoom_auth import configured_accounts  # noqa: WPS433

        for auth in configured_accounts():
            if auth.account_id == account_id:
                account_name = auth.name
                break

    return recording_key(account_name, uuid)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s (%s) — ignoring it.", path, exc)
        return {}


def load_processed_keys() -> set[str]:
    """Return all recordings processed by any ingestion path."""
    with _state_lock:
        processed: set[str] = set()
        for path in (POLL_STATE_FILE, BACKFILL_STATE_FILE):
            data = _load_json(path)
            processed.update(data.get("processed", []))
        return processed


def mark_processed(key: str) -> None:
    """Persist a processed recording key for other ingestion paths to skip."""
    with _state_lock:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        data = _load_json(POLL_STATE_FILE)
        processed = set(data.get("processed", []))
        if key in processed:
            return

        processed.add(key)
        tmp_path = POLL_STATE_FILE.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump({"processed": sorted(processed)}, fh, indent=2)
        tmp_path.replace(POLL_STATE_FILE)
