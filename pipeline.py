"""
Zoom Recording Pipeline Orchestrator
=====================================
Ties together trim_video.py, upload_youtube.py, canva_thumbnail.py, and
post_to_replay_library.py into a full end-to-end pipeline triggered by a
Zoom recording.completed event.

Usage:
    python pipeline.py --webhook          # Start Flask webhook server on port 5055
    python pipeline.py --file payload.json  # Run from a local JSON payload file (testing)

Environment variables (subset — see SETUP.md for full list):

| Variable                    | Required | Description                                        |
|-----------------------------|----------|----------------------------------------------------||
| ZOOM_JWARD_* / ZOOM_NAVIGATORS_* | yes | Server-to-Server OAuth credentials per Zoom account |
| ZOOM_JWARD_WEBHOOK_SECRET   | yes*     | HMAC secret for jward webhook validation           |
| ZOOM_NAVIGATORS_WEBHOOK_SECRET | yes*  | HMAC secret for navigators webhook validation      |
| YOUTUBE_PLAYLIST_NAME       | yes      | Target YouTube playlist name                       |
| WP_BASE_URL                 | yes      | WordPress site base URL (no trailing slash)        |
| WP_USER                     | yes      | WordPress username                                 |
| WP_APP_PASSWORD             | yes      | WordPress Application Password                     |
| TEMP_DIR                    | no       | Temp directory for pipeline files (default /tmp/…) |
| CANVA_CLIENT_ID             | no       | Canva OAuth client ID — omit to skip thumbnail step|
| CANVA_THUMBNAIL_FOLDER_NAME | no       | Canva folder name (default: Replay Thumbnail Folder)|
| CANVA_THUMBNAIL_FOLDER_ID   | no       | Optional folder ID fallback if name lookup fails   |
| WP_REPLAY_CPT               | no       | WordPress CPT slug (default: gc_replay, production: replay) |
| GOOGLE_SHEETS_SPREADSHEET_ID | no      | Replay Library Tracker sheet ID (optional logging) |
| GOOGLE_SERVICE_ACCOUNT_JSON | no       | Service account JSON for Sheets API (share sheet with SA email) |
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import load_dotenv

from processing_state import (
    load_processed_keys,
    mark_processed,
    recording_key_from_payload,
)
from replay_tracker import (
    account_label_from_payload,
    is_configured as tracker_configured,
    log_pipeline_result,
    recording_id_from_payload,
)
from zoom_auth import download_recording as zoom_download_recording

# ---------------------------------------------------------------------------
# Load environment variables
# ---------------------------------------------------------------------------
load_dotenv()

ZOOM_WEBHOOK_SECRET_JWARD   = os.getenv("ZOOM_JWARD_WEBHOOK_SECRET", "")
ZOOM_WEBHOOK_SECRET_NAV     = os.getenv("ZOOM_NAVIGATORS_WEBHOOK_SECRET", "")
YOUTUBE_PLAYLIST_NAME   = os.getenv("YOUTUBE_PLAYLIST_NAME", "Replays")
WP_BASE_URL             = os.getenv("WP_BASE_URL", "")
WP_USER                 = os.getenv("WP_USER", "")
WP_APP_PASSWORD         = os.getenv("WP_APP_PASSWORD", "")
TEMP_DIR                = os.getenv("TEMP_DIR", "/tmp/zoom_pipeline")
CANVA_CLIENT_ID         = os.getenv("CANVA_CLIENT_ID", "")
CANVA_THUMBNAIL_FOLDER_ID = os.getenv("CANVA_THUMBNAIL_FOLDER_ID", "")
WP_REPLAY_CPT           = os.getenv("WP_REPLAY_CPT", "gc_replay")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "pipeline.log"

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

logger = logging.getLogger("zoom_pipeline")
logger.setLevel(logging.DEBUG)
logger.addHandler(_file_handler)
logger.addHandler(_stream_handler)

_active_pipeline_keys: set[str] = set()
_active_pipeline_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_filename(topic: str) -> str:
    """Sanitise a topic string for use in file names."""
    return re.sub(r"[^\w\-]+", "_", topic).strip("_")


def _parse_start_time(start_time_str: str) -> datetime:
    """Parse ISO-8601 start_time from Zoom payload into a UTC datetime."""
    # Python 3.7+ fromisoformat doesn't handle the trailing Z
    return datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))


def build_description(topic: str, date: datetime, duration_minutes: int) -> str:
    """
    Build a YouTube / WordPress description from recording metadata.
    Override this function for custom descriptions.
    """
    date_str = date.strftime("%B %-d, %Y")
    return (
        f"{topic}\n\n"
        f"Recorded: {date_str}\n"
        f"Duration: {duration_minutes} minutes\n\n"
        f"This recording is part of the Ganjier Guild replay library."
    )


def build_title(topic: str, date: datetime) -> str:
    """Build a human-readable video title."""
    date_str = date.strftime("%B %-d, %Y")
    return f"{topic} \u2014 {date_str}"


# ---------------------------------------------------------------------------
# Step 1 – Download
# ---------------------------------------------------------------------------

def download_recording(download_url: str, dest_path: str, account_id: str | None = None) -> str:
    """
    Download the MP4 from Zoom using Server-to-Server OAuth credentials.
    """
    logger.info("Downloading Zoom recording from %s → %s", download_url, dest_path)
    result = zoom_download_recording(download_url, dest_path, account_id=account_id)
    size_mb = os.path.getsize(result) / (1024 * 1024)
    logger.info("Download complete (%.1f MB): %s", size_mb, result)
    return result


# ---------------------------------------------------------------------------
# Step 2 – Trim
# ---------------------------------------------------------------------------

def find_transcript_file(recording_files: list[dict]) -> dict | None:
    """Return the best completed transcript file from a Zoom recording payload."""
    for file_type in ("TRANSCRIPT", "CC"):
        for rec_file in recording_files:
            if (
                rec_file.get("file_type", "").upper() == file_type
                and rec_file.get("status", "").lower() == "completed"
                and rec_file.get("download_url")
            ):
                return rec_file
    return None


def run_trim(
    input_path: str,
    output_path: str,
    transcript_path: str | None = None,
) -> str:
    """
    Call trim_recording() from trim_video.py.

    Parameters
    ----------
    input_path : str
        Path to the raw downloaded MP4.
    output_path : str
        Desired path for the trimmed MP4.
    transcript_path : str or None
        Optional Zoom VTT transcript for phrase-based trim start.

    Returns
    -------
    str
        Path to the trimmed file.
    """
    logger.info("Trimming video: %s → %s", input_path, output_path)

    # Lazy import so the orchestrator can be imported even if trim_video is missing
    try:
        from trim_video import trim_video  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "Could not import trim_video from trim_video.py. "
            "Make sure trim_video.py is in the same directory."
        ) from exc

    result = trim_video(input_path, output_path, transcript_path=transcript_path)
    trimmed_path = result["output_path"] if isinstance(result, dict) else output_path

    if not os.path.exists(trimmed_path):
        raise FileNotFoundError(
            f"Trim step did not produce expected output at {trimmed_path}"
        )

    size_mb = os.path.getsize(trimmed_path) / (1024 * 1024)
    start_method = result.get("start_method", "silence") if isinstance(result, dict) else "silence"
    logger.info(
        "Trim complete (%.1f MB, start=%s): %s",
        size_mb,
        start_method,
        trimmed_path,
    )
    return trimmed_path


# ---------------------------------------------------------------------------
# Step 2b – Intro
# ---------------------------------------------------------------------------

def run_intro(
    trimmed_path: str,
    meeting_title: str,
    output_path: str,
    meeting_date: datetime | None = None,
) -> str:
    """
    Optionally prepend a branded YouTube intro to the trimmed replay.

    Controlled by REPLAY_INTRO_ENABLED in .env. When disabled, returns trimmed_path.
    """
    try:
        from replay_intro import intro_enabled, prepare_upload_video  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "Could not import replay_intro.py. Make sure replay_intro.py is in the same directory."
        ) from exc

    if not intro_enabled():
        logger.info("REPLAY_INTRO_ENABLED is false — skipping intro step.")
        return trimmed_path

    logger.info("Prepending replay intro for '%s'", meeting_title)
    result_path = prepare_upload_video(
        trimmed_path=trimmed_path,
        meeting_title=meeting_title,
        output_path=output_path,
        meeting_date=meeting_date,
    )
    upload_path = str(result_path)

    if not os.path.exists(upload_path):
        raise FileNotFoundError(f"Intro step did not produce expected output at {upload_path}")

    size_mb = os.path.getsize(upload_path) / (1024 * 1024)
    logger.info("Intro step complete (%.1f MB): %s", size_mb, upload_path)
    return upload_path


# ---------------------------------------------------------------------------
# Step 3 – Upload to YouTube
# ---------------------------------------------------------------------------

def run_youtube_upload(
    trimmed_path: str,
    title: str,
    description: str,
    playlist_name: str = YOUTUBE_PLAYLIST_NAME,
) -> str:
    """
    Call upload_video() from upload_youtube.py.

    Parameters
    ----------
    trimmed_path : str
        Path to the trimmed MP4 to upload.
    title : str
        Video title.
    description : str
        Video description.
    playlist_name : str
        YouTube playlist to add the video to.

    Returns
    -------
    str
        The YouTube video ID.
    """
    logger.info("Uploading to YouTube: '%s'", title)

    try:
        from upload_youtube import (  # type: ignore
            get_authenticated_service,
            get_or_create_playlist,
            upload_video,
        )
    except ImportError as exc:
        raise ImportError(
            "Could not import upload helpers from upload_youtube.py. "
            "Make sure upload_youtube.py is in the same directory."
        ) from exc

    youtube = get_authenticated_service()
    playlist_id = get_or_create_playlist(youtube, playlist_name)
    result = upload_video(
        video_path=trimmed_path,
        title=title,
        description=description,
        tags=[],
        playlist_id=playlist_id,
    )

    video_id = result.get("video_id") if isinstance(result, dict) else result
    if not video_id:
        raise RuntimeError("upload_video() returned an empty video ID.")

    logger.info("YouTube upload complete. Video ID: %s", video_id)
    return video_id


# ---------------------------------------------------------------------------
# Step 4b – Canva Thumbnail
# ---------------------------------------------------------------------------

def run_canva_thumbnail(meeting_title: str, meeting_date: datetime) -> str | None:
    """
    Fetch a matching Canva thumbnail PNG for the given meeting.

    Parameters
    ----------
    meeting_title : str
        The Zoom meeting topic, used to fuzzy-match a design in the Canva folder.
    meeting_date : datetime
        The meeting start time (UTC), passed to get_thumbnail for date matching.

    Returns
    -------
    str or None
        Absolute path to the downloaded PNG, or None if Canva is not configured
        or no matching design was found.
    """
    if not CANVA_CLIENT_ID:
        logger.info("CANVA_CLIENT_ID not set — skipping Canva thumbnail step.")
        return None

    try:
        from canva_thumbnail import get_thumbnail  # type: ignore
    except ImportError as exc:
        logger.warning(
            "Could not import get_thumbnail from canva_thumbnail.py — skipping: %s", exc
        )
        return None

    try:
        png_path = get_thumbnail(
            meeting_title=meeting_title,
            meeting_date=meeting_date,
            output_dir=TEMP_DIR,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Canva thumbnail step raised an exception — skipping: %s", exc)
        return None

    return png_path if isinstance(png_path, str) else None


# ---------------------------------------------------------------------------
# Step 5 – Post to WordPress Replay Library
# ---------------------------------------------------------------------------

def run_wp_post(
    video_id: str,
    title: str,
    description: str,
    date: datetime,
    local_thumbnail_path: str | None = None,
) -> dict:
    """
    Call create_replay_post() from post_to_replay_library.py.

    Parameters
    ----------
    video_id : str
        YouTube video ID.
    title : str
        Post title.
    description : str
        Post body / description.
    date : datetime
        Recording date (UTC).
    local_thumbnail_path : str or None
        Optional path to a local PNG to upload as the post thumbnail.
        If None, post_to_replay_library.py falls back to the YouTube
        auto-generated thumbnail.
    """
    logger.info("Posting to WordPress replay library: '%s' (video_id=%s)", title, video_id)

    try:
        from post_to_replay_library import create_replay_post  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "Could not import create_replay_post from post_to_replay_library.py. "
            "Make sure post_to_replay_library.py is in the same directory."
        ) from exc

    kwargs: dict = dict(
        video_id=video_id,
        title=title,
        description=description,
        date=date,
        cpt_endpoint=f"/wp-json/wp/v2/{WP_REPLAY_CPT}",
    )
    if local_thumbnail_path is not None:
        kwargs["local_thumbnail_path"] = local_thumbnail_path

    result = create_replay_post(**kwargs)

    logger.info("WordPress post created successfully.")
    return result


# ---------------------------------------------------------------------------
# Step 5b – Link MEC calendar event
# ---------------------------------------------------------------------------

def run_mec_link(
    topic: str,
    start_dt: datetime,
    replay_url: str,
    youtube_url: str,
    replay_post_id: int,
) -> dict | None:
    """Match the recording to a Modern Events Calendar event and attach replay links."""
    try:
        from mec_events import link_recording_to_mec_event  # type: ignore
    except ImportError as exc:
        logger.warning("Could not import mec_events.py — skipping MEC link: %s", exc)
        return None

    return link_recording_to_mec_event(
        topic=topic,
        start_dt=start_dt,
        replay_url=replay_url,
        youtube_url=youtube_url,
        replay_post_id=replay_post_id,
    )


# ---------------------------------------------------------------------------
# Step 6 – Cleanup
# ---------------------------------------------------------------------------

def cleanup_files(*paths: str) -> None:
    """Delete temporary files after a successful pipeline run."""
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info("Cleaned up temp file: %s", path)
            except OSError as exc:
                logger.warning("Could not delete %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def _log_tracker_failure(
    payload: dict,
    topic: str,
    start_dt: datetime,
    duration: int,
    step: str,
    exc: Exception,
) -> None:
    if not tracker_configured():
        return
    log_pipeline_result(
        topic=topic,
        recording_date=start_dt,
        duration_minutes=duration,
        status=f"failed ({step})",
        zoom_account=account_label_from_payload(payload),
        error=str(exc),
        recording_id=recording_id_from_payload(payload),
    )


def run_pipeline(payload: dict) -> None:
    """
    Execute the full Zoom → trim → YouTube → WordPress pipeline.

    Parameters
    ----------
    payload : dict
        Parsed Zoom recording.completed webhook payload.

    Raises
    ------
    SystemExit
        Exits with code 1 on any step failure (files are preserved for debugging).
    """
    logger.info("=" * 60)
    logger.info("Pipeline started at %s", datetime.now(timezone.utc).isoformat())

    # ------------------------------------------------------------------
    # Extract metadata from payload
    # ------------------------------------------------------------------
    try:
        obj           = payload["payload"]["object"]
        topic         = obj["topic"]
        start_time_s  = obj["start_time"]
        duration      = int(obj.get("duration", 0))
        rec_files     = obj["recording_files"]
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Malformed payload — missing required field: %s", exc)
        sys.exit(1)

    # Find the MP4 recording with status == "completed"
    mp4_file = next(
        (
            f for f in rec_files
            if f.get("file_type", "").upper() == "MP4"
            and f.get("status", "").lower() == "completed"
        ),
        None,
    )
    if mp4_file is None:
        logger.error("No completed MP4 recording found in payload.")
        sys.exit(1)

    download_url  = mp4_file["download_url"]
    account_id    = payload.get("payload", {}).get("account_id")
    start_dt      = _parse_start_time(start_time_s)
    date_tag      = start_dt.strftime("%Y%m%d")
    clean_topic   = _clean_filename(topic)

    os.makedirs(TEMP_DIR, exist_ok=True)
    raw_path      = os.path.join(TEMP_DIR, f"zoom_{date_tag}_{clean_topic}.mp4")
    trimmed_path  = os.path.join(TEMP_DIR, f"zoom_{date_tag}_{clean_topic}_trimmed.mp4")
    transcript_path: str | None = None
    transcript_file = find_transcript_file(rec_files)
    upload_path   = os.path.join(TEMP_DIR, f"zoom_{date_tag}_{clean_topic}_upload.mp4")

    title       = build_title(topic, start_dt)
    description = build_description(topic, start_dt, duration)

    logger.info("Topic     : %s", topic)
    logger.info("Start     : %s", start_dt.isoformat())
    logger.info("Duration  : %d min", duration)
    logger.info("Title     : %s", title)
    logger.info("Raw path  : %s", raw_path)
    logger.info("Trim path : %s", trimmed_path)

    # ------------------------------------------------------------------
    # Step 1 – Download
    # ------------------------------------------------------------------
    logger.info("[1/7] Downloading recording …")
    t0 = time.monotonic()
    try:
        download_recording(download_url, raw_path, account_id=account_id)
    except Exception as exc:
        logger.error("[1/7] Download failed: %s", exc, exc_info=True)
        _log_tracker_failure(payload, topic, start_dt, duration, "download", exc)
        logger.error("Preserving temp files for debugging. Exiting.")
        sys.exit(1)
    logger.info("[1/7] Download finished in %.1fs", time.monotonic() - t0)

    if transcript_file:
        transcript_path = os.path.join(TEMP_DIR, f"zoom_{date_tag}_{clean_topic}.vtt")
        try:
            download_recording(
                transcript_file["download_url"],
                transcript_path,
                account_id=account_id,
            )
            logger.info("[1/7] Transcript downloaded for phrase-based trim: %s", transcript_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[1/7] Transcript download failed — trim will fall back to silence detection: %s",
                exc,
            )
            transcript_path = None
    else:
        logger.info("[1/7] No transcript file in recording payload — silence-based trim start.")

    # ------------------------------------------------------------------
    # Step 2 – Trim
    # ------------------------------------------------------------------
    logger.info("[2/7] Trimming video …")
    t0 = time.monotonic()
    try:
        run_trim(raw_path, trimmed_path, transcript_path=transcript_path)
    except Exception as exc:
        logger.error("[2/7] Trim failed: %s", exc, exc_info=True)
        _log_tracker_failure(payload, topic, start_dt, duration, "trim", exc)
        logger.error("Preserving temp files for debugging. Exiting.")
        sys.exit(1)
    logger.info("[2/7] Trim finished in %.1fs", time.monotonic() - t0)

    # ------------------------------------------------------------------
    # Step 2b – Intro
    # ------------------------------------------------------------------
    logger.info("[2b/7] Preparing intro …")
    t0 = time.monotonic()
    try:
        final_upload_path = run_intro(trimmed_path, topic, upload_path, start_dt)
    except Exception as exc:
        logger.error("[2b/7] Intro step failed: %s", exc, exc_info=True)
        _log_tracker_failure(payload, topic, start_dt, duration, "intro", exc)
        logger.error("Preserving temp files for debugging. Exiting.")
        sys.exit(1)
    logger.info("[2b/7] Intro step finished in %.1fs", time.monotonic() - t0)

    # ------------------------------------------------------------------
    # Step 3 – Upload to YouTube
    # ------------------------------------------------------------------
    logger.info("[3/7] Uploading to YouTube …")
    t0 = time.monotonic()
    try:
        video_id = run_youtube_upload(
            final_upload_path,
            title,
            description,
            YOUTUBE_PLAYLIST_NAME,
        )
    except Exception as exc:
        logger.error("[3/7] YouTube upload failed: %s", exc, exc_info=True)
        _log_tracker_failure(payload, topic, start_dt, duration, "youtube", exc)
        logger.error("Preserving temp files for debugging. Exiting.")
        sys.exit(1)
    logger.info("[3/7] YouTube upload finished in %.1fs", time.monotonic() - t0)

    # ------------------------------------------------------------------
    # Step 4b – Canva Thumbnail
    # ------------------------------------------------------------------
    logger.info("[4/7] Fetching Canva thumbnail …")
    canva_thumbnail_path = run_canva_thumbnail(topic, start_dt)
    if canva_thumbnail_path:
        logger.info("[4/7] Canva thumbnail fetched: %s", canva_thumbnail_path)
    else:
        logger.info("[4/7] Canva thumbnail unavailable, using YouTube auto-thumbnail")

    # ------------------------------------------------------------------
    # Step 5 – Post to WordPress
    # ------------------------------------------------------------------
    logger.info("[5/7] Creating WordPress replay post …")
    t0 = time.monotonic()
    try:
        wp_result = run_wp_post(
            video_id,
            title,
            description,
            start_dt,
            local_thumbnail_path=canva_thumbnail_path,
        )
    except Exception as exc:
        logger.error("[5/7] WordPress post failed: %s", exc, exc_info=True)
        _log_tracker_failure(payload, topic, start_dt, duration, "wordpress", exc)
        logger.error("Preserving temp files for debugging. Exiting.")
        sys.exit(1)
    logger.info("[5/7] WordPress post finished in %.1fs", time.monotonic() - t0)

    # ------------------------------------------------------------------
    # Step 5b – Link MEC calendar event
    # ------------------------------------------------------------------
    logger.info("[5b/7] Linking MEC calendar event …")
    t0 = time.monotonic()
    mec_result = run_mec_link(
        topic,
        start_dt,
        wp_result.get("wp_post_url", ""),
        wp_result.get("youtube_url", f"https://www.youtube.com/watch?v={video_id}"),
        int(wp_result.get("wp_post_id", 0) or 0),
    )
    if mec_result:
        logger.info(
            "[5b/7] MEC event linked: %s",
            mec_result.get("event_url") or mec_result.get("match", {}).get("url"),
        )
    else:
        logger.info("[5b/7] No MEC calendar event linked")
    logger.info("[5b/7] MEC link finished in %.1fs", time.monotonic() - t0)

    # ------------------------------------------------------------------
    # Step 6 – Cleanup
    # ------------------------------------------------------------------
    logger.info("[6/7] Cleaning up temp files …")
    cleanup_files(
        raw_path,
        trimmed_path,
        final_upload_path if final_upload_path != trimmed_path else None,
        canva_thumbnail_path if canva_thumbnail_path and canva_thumbnail_path.startswith(TEMP_DIR) else None,
    )

    log_pipeline_result(
        topic=topic,
        recording_date=start_dt,
        duration_minutes=duration,
        status="completed",
        zoom_account=account_label_from_payload(payload),
        youtube_url=wp_result.get("youtube_url", f"https://www.youtube.com/watch?v={video_id}"),
        wp_url=wp_result.get("wp_post_url", ""),
        mec_event_url=(mec_result or {}).get("event_url")
        or ((mec_result or {}).get("match") or {}).get("url", ""),
        recording_id=recording_id_from_payload(payload),
    )

    logger.info("Pipeline completed successfully for: %s", title)
    logger.info("Thumbnail used: %s", canva_thumbnail_path or "YouTube auto-thumbnail")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Flask webhook server
# ---------------------------------------------------------------------------

def _verify_zoom_signature(request_body: bytes, timestamp: str, signature: str) -> bool:
    """
    Validate the Zoom webhook signature against both accounts' secrets.

    Zoom signs requests with:
        HMAC-SHA256( "v0:{timestamp}:{body}", secret )

    The header value format is "v0=<hex_digest>".
    Accepts a valid signature from EITHER the jward OR navigators secret.
    """
    secrets = [
        s for s in [ZOOM_WEBHOOK_SECRET_JWARD, ZOOM_WEBHOOK_SECRET_NAV] if s
    ]
    if not secrets:
        logger.warning(
            "Neither ZOOM_JWARD_WEBHOOK_SECRET nor ZOOM_NAVIGATORS_WEBHOOK_SECRET is set "
            "— skipping signature check."
        )
        return True

    message = f"v0:{timestamp}:{request_body.decode('utf-8')}"
    for secret in secrets:
        expected = (
            "v0="
            + hmac.new(
                secret.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        )
        if hmac.compare_digest(expected, signature):
            return True
    return False


def run_webhook_server() -> None:
    """Start the Flask webhook server on port 5055."""
    try:
        from flask import Flask, jsonify, request as flask_request  # type: ignore
    except ImportError as exc:
        logger.error("Flask is not installed. Run: pip install flask")
        raise

    app = Flask(__name__)

    @app.route("/zoom/webhook", methods=["POST"])
    def zoom_webhook():
        body        = flask_request.get_data()
        timestamp   = flask_request.headers.get("x-zm-request-timestamp", "")
        signature   = flask_request.headers.get("x-zm-signature", "")

        # ------------------------------------------------------------------
        # Endpoint validation challenge (Zoom requires this on first setup)
        # ------------------------------------------------------------------
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.warning("Received non-JSON body.")
            return jsonify({"error": "invalid JSON"}), 400

        if payload.get("event") == "endpoint.url_validation":
            plain_token = payload.get("payload", {}).get("plainToken", "")
            # Use whichever secret is available (jward first, then navigators)
            _secret = ZOOM_WEBHOOK_SECRET_JWARD or ZOOM_WEBHOOK_SECRET_NAV
            if _secret:
                encrypted_token = hmac.new(
                    _secret.encode("utf-8"),
                    plain_token.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
            else:
                encrypted_token = plain_token  # fallback (secret not configured)
            logger.info("Responding to Zoom endpoint validation challenge.")
            return jsonify({"plainToken": plain_token, "encryptedToken": encrypted_token})

        # ------------------------------------------------------------------
        # Signature verification for all other events
        # ------------------------------------------------------------------
        if not _verify_zoom_signature(body, timestamp, signature):
            logger.warning("Invalid Zoom webhook signature — rejecting request.")
            return jsonify({"error": "invalid signature"}), 401

        event_type = payload.get("event", "")
        logger.info("Received Zoom event: %s", event_type)

        if event_type == "recording.completed":
            recording_key = recording_key_from_payload(payload)
            if recording_key and recording_key in load_processed_keys():
                logger.info("Recording already processed; skipping: %s", recording_key)
                return jsonify({"status": "skipped", "reason": "already_processed"}), 200

            if recording_key:
                with _active_pipeline_lock:
                    if recording_key in _active_pipeline_keys:
                        logger.info("Pipeline already running for recording: %s", recording_key)
                        return jsonify({"status": "accepted", "reason": "already_running"}), 200
                    _active_pipeline_keys.add(recording_key)

            logger.info("Triggering pipeline for recording.completed event.")
            # Respond immediately so Zoom does not time out; process in background.
            def _run_pipeline_safe() -> None:
                try:
                    run_pipeline(payload)
                except SystemExit as exc:
                    logger.error("Pipeline exited with code %s", exc.code)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Pipeline failed: %s", exc, exc_info=True)
                else:
                    if recording_key:
                        mark_processed(recording_key)
                finally:
                    if recording_key:
                        with _active_pipeline_lock:
                            _active_pipeline_keys.discard(recording_key)

            threading.Thread(target=_run_pipeline_safe, daemon=True).start()
            return jsonify({"status": "accepted"}), 200

        # Acknowledge but ignore other event types
        return jsonify({"status": "ignored", "event": event_type}), 200

    logger.info("Starting Zoom webhook server on port 5055 …")
    app.run(host="0.0.0.0", port=5055, debug=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zoom Recording Pipeline Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--webhook",
        action="store_true",
        help="Start the Flask webhook server (listens on port 5055).",
    )
    group.add_argument(
        "--file",
        metavar="PAYLOAD_JSON",
        help="Path to a local JSON file containing a Zoom recording.completed payload.",
    )
    args = parser.parse_args()

    if args.webhook:
        run_webhook_server()

    elif args.file:
        payload_path = Path(args.file)
        if not payload_path.exists():
            logger.error("File not found: %s", payload_path)
            sys.exit(1)
        try:
            with open(payload_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse JSON from %s: %s", payload_path, exc)
            sys.exit(1)

        event_type = payload.get("event", "")
        if event_type != "recording.completed":
            logger.warning(
                "Payload event type is '%s', expected 'recording.completed'. Proceeding anyway.",
                event_type,
            )

        run_pipeline(payload)


if __name__ == "__main__":
    main()
