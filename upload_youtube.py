"""
upload_youtube.py — Ganjier Guild Zoom Pipeline
================================================
Uploads a video to YouTube via the YouTube Data API v3 using OAuth2.

OAuth2 Setup Steps
------------------
1. Go to https://console.cloud.google.com/
2. Create a project (or select an existing one).
3. Enable the "YouTube Data API v3" under APIs & Services > Library.
4. Go to APIs & Services > Credentials > Create Credentials > OAuth client ID.
5. Choose "Desktop App" as the application type.
6. Download the resulting JSON file and save it as `client_secrets.json`
   in the same directory as this script (or pass its path via --secrets).
7. On first run, a browser window will open to authorize the app.
   The resulting token is saved to `token.json` and reused on subsequent runs.
8. Required OAuth scope: https://www.googleapis.com/auth/youtube

Dependencies
------------
    pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
"""

import argparse
import http.client
import json
import logging
import os
import random
import time
from datetime import datetime
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/youtube"]

# Default file paths — override via function args or CLI flags
DEFAULT_TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")
DEFAULT_SECRETS_PATH = os.path.join(os.path.dirname(__file__), "client_secrets.json")

# Retry settings
MAX_RETRIES = 3
RETRY_STATUS_CODES = {500, 502, 503, 504}
RETRIABLE_EXCEPTIONS = (
    http.client.HTTPException,
    IOError,
    OSError,
)

# Ganjier Guild branding
GANJIER_FOOTER = (
    "──────────────────────────────────────\n"
    "🌿 Ganjier Guild\n"
    "The professional community for cannabis sommeliers.\n"
    "🌐 https://ganjierguild.com\n"
    "📋 Memberships · Events · Voyages · Directory\n"
    "──────────────────────────────────────"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OAuth2 helpers
# ---------------------------------------------------------------------------


def get_authenticated_service(
    token_path: str = DEFAULT_TOKEN_PATH,
    secrets_path: str = DEFAULT_SECRETS_PATH,
) -> object:
    """
    Return an authorised YouTube API service object.

    Loads existing credentials from *token_path* when available; otherwise
    runs the local-server OAuth2 flow using *secrets_path* and persists the
    resulting token for future runs.
    """
    creds: Optional[Credentials] = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Refresh or re-authorise as needed
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired OAuth2 token.")
            creds.refresh(Request())
        else:
            if not os.path.exists(secrets_path):
                raise FileNotFoundError(
                    f"client_secrets.json not found at {secrets_path!r}. "
                    "See the OAuth2 Setup Steps at the top of this file."
                )
            flow = InstalledAppFlow.from_client_secrets_file(secrets_path, SCOPES)
            # Opens a local browser tab; uses port 0 so the OS picks a free port
            creds = flow.run_local_server(port=0)

        # Persist token for next run
        with open(token_path, "w") as fh:
            fh.write(creds.to_json())
        logger.info("Token saved to %s", token_path)

    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Description builder
# ---------------------------------------------------------------------------


def build_description(
    meeting_name: str,
    date: datetime,
    topics: Optional[list] = None,
    custom_footer: Optional[str] = None,
) -> str:
    """
    Assemble a nicely formatted YouTube video description with Ganjier Guild branding.

    Parameters
    ----------
    meeting_name : str
        Human-readable name of the meeting or session.
    date : datetime
        Date/time the meeting took place.
    topics : list, optional
        Bullet-point list of topics covered.
    custom_footer : str, optional
        Additional footer text inserted *before* the Guild branding block.

    Returns
    -------
    str
        Formatted description string ready to pass to upload_video().
    """
    date_str = date.strftime("%A, %B %-d, %Y at %-I:%M %p %Z").strip()

    lines = [
        f"📅 {meeting_name}",
        f"🗓  Recorded: {date_str}",
        "",
    ]

    if topics:
        lines.append("Topics covered:")
        for topic in topics:
            lines.append(f"  • {topic}")
        lines.append("")

    if custom_footer:
        lines.append(custom_footer.strip())
        lines.append("")

    lines.append(GANJIER_FOOTER)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Playlist helpers
# ---------------------------------------------------------------------------


def get_or_create_playlist(
    youtube,
    playlist_name: str,
    description: str = "",
    privacy_status: str = "unlisted",
) -> str:
    """
    Return the playlist ID for *playlist_name*, creating it if it does not exist.

    Searches the authenticated channel's playlists first.  If no match is
    found, a new playlist is created with the given *description* and
    *privacy_status*.

    Parameters
    ----------
    youtube
        Authenticated YouTube API service object.
    playlist_name : str
        Exact display name of the playlist to find or create.
    description : str, optional
        Description used only when creating a new playlist.
    privacy_status : str, optional
        Privacy level for a newly created playlist ("unlisted", "public", "private").

    Returns
    -------
    str
        The playlist ID.
    """
    # --- Search existing playlists ---
    request = youtube.playlists().list(
        part="snippet",
        mine=True,
        maxResults=50,
    )
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            if item["snippet"]["title"].lower() == playlist_name.lower():
                playlist_id = item["id"]
                logger.info("Found existing playlist %r (id=%s)", playlist_name, playlist_id)
                return playlist_id
        request = youtube.playlists().list_next(request, response)

    # --- Create new playlist ---
    logger.info("Playlist %r not found — creating.", playlist_name)
    body = {
        "snippet": {
            "title": playlist_name,
            "description": description,
        },
        "status": {
            "privacyStatus": privacy_status,
        },
    }
    response = youtube.playlists().insert(part="snippet,status", body=body).execute()
    playlist_id = response["id"]
    logger.info("Created playlist %r (id=%s)", playlist_name, playlist_id)
    return playlist_id


def add_video_to_playlist(youtube, video_id: str, playlist_id: str) -> None:
    """Insert *video_id* into *playlist_id*."""
    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            },
        }
    }
    youtube.playlistItems().insert(part="snippet", body=body).execute()
    logger.info("Added video %s to playlist %s", video_id, playlist_id)


# ---------------------------------------------------------------------------
# Resumable upload with retry
# ---------------------------------------------------------------------------


def _execute_with_retry(request):
    """
    Execute a resumable upload *request* with up to MAX_RETRIES retries.

    Uses exponential back-off with random jitter for transient errors.
    Returns the completed API response dict.
    """
    response = None
    error = None
    retry = 0

    while response is None:
        try:
            logger.info("Uploading… (attempt %d)", retry + 1)
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info("Upload progress: %d%%", pct)
        except HttpError as exc:
            if exc.resp.status in RETRY_STATUS_CODES:
                error = f"HTTP {exc.resp.status}: {exc}"
            else:
                raise  # Non-retryable HTTP error — propagate immediately
        except RETRIABLE_EXCEPTIONS as exc:
            error = str(exc)

        if error:
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(
                    f"Upload failed after {MAX_RETRIES} retries. Last error: {error}"
                )
            sleep_seconds = (2 ** retry) + random.uniform(0, 1)
            logger.warning(
                "Transient error (%s). Retrying in %.1f s… (%d/%d)",
                error,
                sleep_seconds,
                retry,
                MAX_RETRIES,
            )
            time.sleep(sleep_seconds)
            error = None  # reset for next attempt

    return response


# ---------------------------------------------------------------------------
# Main upload function
# ---------------------------------------------------------------------------


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list,
    playlist_id: Optional[str] = None,
    token_path: str = DEFAULT_TOKEN_PATH,
    secrets_path: str = DEFAULT_SECRETS_PATH,
    category_id: str = "27",  # 27 = Education
) -> dict:
    """
    Upload a video to YouTube and optionally add it to a playlist.

    Parameters
    ----------
    video_path : str
        Absolute or relative path to the video file.
    title : str
        YouTube video title (max 100 characters).
    description : str
        YouTube video description (max 5000 characters).
    tags : list of str
        List of tags/keywords for the video.
    playlist_id : str, optional
        YouTube playlist ID to add the video to after upload.
    token_path : str, optional
        Path to the OAuth2 token JSON file (default: token.json next to script).
    secrets_path : str, optional
        Path to the client_secrets.json file (default: next to script).
    category_id : str, optional
        YouTube video category numeric ID (default "27" = Education).

    Returns
    -------
    dict
        {
            "video_id": str,
            "youtube_url": str,
            "title": str,
            "privacy_status": str,
        }
    """
    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path!r}")

    youtube = get_authenticated_service(token_path=token_path, secrets_path=secrets_path)

    body = {
        "snippet": {
            "title": title[:100],  # YouTube hard limit
            "description": description[:5000],  # YouTube hard limit
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": "unlisted",
            "selfDeclaredMadeForKids": False,
        },
    }

    # MediaFileUpload with resumable=True handles large files in chunks
    media = MediaFileUpload(
        video_path,
        mimetype="video/*",
        resumable=True,
        chunksize=256 * 1024,  # 256 KB chunks
    )

    insert_request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    logger.info("Starting resumable upload: %r", video_path)
    response = _execute_with_retry(insert_request)

    video_id = response["id"]
    youtube_url = f"https://youtu.be/{video_id}"
    privacy_status = response["status"]["privacyStatus"]

    logger.info("Upload complete — video_id=%s url=%s", video_id, youtube_url)

    # Optionally add to playlist
    if playlist_id:
        add_video_to_playlist(youtube, video_id, playlist_id)

    return {
        "video_id": video_id,
        "youtube_url": youtube_url,
        "title": response["snippet"]["title"],
        "privacy_status": privacy_status,
    }


# ---------------------------------------------------------------------------
# CLI / __main__ block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Upload a video to YouTube (Ganjier Guild pipeline).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--test-auth", action="store_true",
                        help="Run OAuth flow only (no upload). Saves token.json and exits.")
    parser.add_argument("video_path", nargs="?", help="Path to the video file to upload.")
    parser.add_argument("--title", required=False, default=None, help="YouTube video title.")
    parser.add_argument("--description", default="", help="Video description text.")
    parser.add_argument(
        "--tags",
        nargs="*",
        default=[],
        help="Space-separated list of tags, e.g. --tags cannabis guild session",
    )
    parser.add_argument("--playlist-id", default=None, help="Playlist ID to add the video to.")
    parser.add_argument("--token", default=DEFAULT_TOKEN_PATH, help="Path to token.json.")
    parser.add_argument("--secrets", default=DEFAULT_SECRETS_PATH, help="Path to client_secrets.json.")

    # Demo mode: build a sample description from meeting metadata
    parser.add_argument("--meeting-name", default=None, help="Meeting name for auto-description.")
    parser.add_argument(
        "--topics",
        nargs="*",
        default=None,
        help="Topics covered (used with --meeting-name).",
    )

    args = parser.parse_args()

    # --test-auth: just run the OAuth flow and exit
    if args.test_auth:
        print("Running YouTube OAuth flow — a browser window will open...")
        service = get_authenticated_service(
            token_path=args.token,
            secrets_path=args.secrets,
        )
        channel = service.channels().list(part="snippet", mine=True).execute()
        name = channel["items"][0]["snippet"]["title"]
        print(f"✓ Authenticated successfully as YouTube channel: {name}")
        print(f"✓ token.json saved to: {args.token}")
        raise SystemExit(0)

    if not args.video_path or not args.title:
        parser.error("video_path and --title are required unless --test-auth is used.")

    # If meeting-name supplied, auto-build the description
    description = args.description
    if args.meeting_name:
        description = build_description(
            meeting_name=args.meeting_name,
            date=datetime.now(),
            topics=args.topics,
        )
        print("=== Auto-generated description ===")
        print(description)
        print("==================================\n")

    result = upload_video(
        video_path=args.video_path,
        title=args.title,
        description=description,
        tags=args.tags,
        playlist_id=args.playlist_id,
        token_path=args.token,
        secrets_path=args.secrets,
    )

    print(json.dumps(result, indent=2))
