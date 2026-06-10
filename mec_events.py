"""
mec_events.py — Match Zoom recordings to Modern Events Calendar events and link replays.

Requires the Ganjier Replay Pipeline WordPress plugin (gg/v1 REST routes).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))

logger = logging.getLogger("mec_events")

MEC_MATCH_PATH = "/wp-json/gg/v1/mec-events/match"
MEC_LINK_PATH = "/wp-json/gg/v1/mec-events/{event_id}/link-replay"


def mec_enabled() -> bool:
    return os.getenv("MEC_LINK_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _wp_auth() -> tuple[str, str]:
    return (
        os.getenv("WP_USER", ""),
        os.getenv("WP_APP_PASSWORD", "").replace(" ", ""),
    )


def _wp_base_url() -> str:
    return os.getenv("WP_BASE_URL", "").rstrip("/")


def normalize_title(value: str) -> str:
    import re

    text = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return " ".join(text.split())


def title_score(topic: str, event_title: str) -> int:
    """Score title similarity for tests and optional local preview."""
    topic_norm = normalize_title(topic)
    title_norm = normalize_title(event_title)
    if not topic_norm or not title_norm:
        return 0
    if topic_norm in title_norm or title_norm in topic_norm:
        return 100
    # Fallback ratio without external deps.
    topic_tokens = set(topic_norm.split())
    title_tokens = set(title_norm.split())
    if not topic_tokens or not title_tokens:
        return 0
    overlap = len(topic_tokens & title_tokens)
    return int(100 * overlap / max(len(topic_tokens), len(title_tokens)))


def match_mec_event(
    topic: str,
    start_dt: datetime,
    *,
    min_score: int | None = None,
) -> dict[str, Any] | None:
    """Find the best MEC calendar event for a Zoom recording."""
    if not mec_enabled():
        return None

    base_url = _wp_base_url()
    wp_user, wp_password = _wp_auth()
    if not all([base_url, wp_user, wp_password]):
        logger.debug("WordPress credentials missing; skipping MEC match.")
        return None

    if min_score is None:
        min_score = int(os.getenv("MEC_MATCH_MIN_SCORE", "40"))

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    response = requests.get(
        f"{base_url}{MEC_MATCH_PATH}",
        params={
            "topic": topic,
            "start": start_dt.astimezone(timezone.utc).isoformat(),
            "min_score": min_score,
        },
        auth=(wp_user, wp_password),
        timeout=30,
    )

    if response.status_code == 404:
        logger.info("MEC match endpoint not found — install/activate ganjier-replay-pipeline plugin.")
        return None
    if not response.ok:
        raise RuntimeError(f"MEC match failed HTTP {response.status_code}: {response.text[:500]}")

    payload = response.json()
    if not payload.get("matched"):
        return None
    return payload.get("event")


def link_replay_to_mec_event(
    event_id: int,
    *,
    replay_url: str,
    youtube_url: str = "",
    replay_post_id: int = 0,
    occurrence_date: str = "",
) -> dict[str, Any]:
    """Attach replay URLs to a matched MEC event."""
    base_url = _wp_base_url()
    wp_user, wp_password = _wp_auth()
    if not all([base_url, wp_user, wp_password]):
        raise RuntimeError("WP_BASE_URL, WP_USER, and WP_APP_PASSWORD are required for MEC linking.")

    response = requests.post(
        f"{base_url}{MEC_LINK_PATH.format(event_id=event_id)}",
        json={
            "replay_url": replay_url,
            "youtube_url": youtube_url,
            "replay_post_id": replay_post_id,
            "occurrence_date": occurrence_date,
        },
        auth=(wp_user, wp_password),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"MEC link failed HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def link_recording_to_mec_event(
    *,
    topic: str,
    start_dt: datetime,
    replay_url: str,
    youtube_url: str = "",
    replay_post_id: int = 0,
) -> dict[str, Any] | None:
    """
    Match a recording to an MEC event and link the replay.

    Returns link result dict or None when no match is found. Never raises.
    """
    if not mec_enabled():
        return None

    try:
        match = match_mec_event(topic, start_dt)
        if not match:
            logger.info("No MEC calendar event matched topic=%r", topic)
            return None

        result = link_replay_to_mec_event(
            int(match["event_id"]),
            replay_url=replay_url,
            youtube_url=youtube_url,
            replay_post_id=replay_post_id,
            occurrence_date=match.get("occurrence_date", ""),
        )
        logger.info(
            "Linked replay to MEC event %s (%s)",
            match.get("event_id"),
            match.get("title"),
        )
        return {**result, "match": match}
    except Exception as exc:  # noqa: BLE001
        logger.warning("MEC calendar link failed: %s", exc)
        return None
