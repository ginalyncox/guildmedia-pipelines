"""
canva_thumbnail.py — Fetch a Ganjier Guild replay thumbnail from Canva and export it as PNG.

SETUP (OAuth 2.0 — Canva Connect API)
--------------------------------------
1. Open your Canva integration at:
   https://www.canva.com/developers/integrations/connect-api/OC-AZ4jhERgHtNN/authentication
2. Go to the Scopes tab — confirm these are enabled:
     folder:read
     design:content:read
     design:meta:read
3. Click "Generate secret" (or "Client secrets") and copy the value.
4. Add the following lines to your .env file (same directory as this script):

       CANVA_CLIENT_ID=OC-AZ4jhERgHtNN
       CANVA_CLIENT_SECRET=your_canva_client_secret_here
       CANVA_THUMBNAIL_FOLDER_NAME=Replay Thumbnail Folder
       # CANVA_THUMBNAIL_FOLDER_ID=your_folder_id_here   # optional fallback

5. Run the one-time auth flow (opens browser, approve access, token saved):
       python canva_thumbnail.py --auth

6. Find your thumbnail folder ID:
       python canva_thumbnail.py --list-folders

   Then set CANVA_THUMBNAIL_FOLDER_ID=<id> in .env.

7. IMPORTANT: Never commit canva_token.json to git.
   Add it to .gitignore:   echo "canva_token.json" >> .gitignore

REQUIRED CANVA SCOPES (set on the integration)
-----------------------------------------------
  folder:read
  design:content:read
  design:meta:read

USAGE
-----
  python canva_thumbnail.py --auth                           # run auth flow (first-time setup)
  python canva_thumbnail.py --list-folders                   # find your folder ID
  python canva_thumbnail.py --list-folder                    # list designs in CANVA_THUMBNAIL_FOLDER_ID
  python canva_thumbnail.py --match "Guild Monthly Webinar"  # test match logic
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import sys
import time
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
load_dotenv()

CANVA_CLIENT_ID: str = os.getenv("CANVA_CLIENT_ID", "")
CANVA_CLIENT_SECRET: str = os.getenv("CANVA_CLIENT_SECRET", "")
CANVA_THUMBNAIL_FOLDER_ID: str = os.getenv("CANVA_THUMBNAIL_FOLDER_ID", "")
CANVA_THUMBNAIL_FOLDER_NAME: str = os.getenv(
    "CANVA_THUMBNAIL_FOLDER_NAME", "Replay Thumbnail Folder"
)
CANVA_BASE_URL: str = "https://api.canva.com/rest/v1"

# OAuth 2.0 endpoints
CANVA_AUTH_URL: str = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL: str = "https://api.canva.com/rest/v1/oauth/token"

# Redirect URI must match what's configured in Canva integration settings
CANVA_REDIRECT_URI: str = "http://127.0.0.1:8080/canva/callback"
CANVA_REDIRECT_HOST: str = "127.0.0.1"
CANVA_REDIRECT_PORT: int = 8080
CANVA_REDIRECT_PATH: str = "/canva/callback"

CANVA_SCOPES: list[str] = ["folder:read", "design:content:read", "design:meta:read"]

# Token file — sits next to this script (same pattern as token.json for YouTube)
CANVA_TOKEN_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "canva_token.json")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("canva_thumbnail")

# ---------------------------------------------------------------------------
# In-memory folder-listing cache (keyed by folder_id)
# ---------------------------------------------------------------------------
_folder_cache: dict[str, list[dict]] = {}


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_pkce_pair() -> tuple[str, str]:
    """
    Generate a PKCE (code_verifier, code_challenge) pair.

    Returns
    -------
    (code_verifier, code_challenge)
        code_verifier  — random high-entropy URL-safe string (43–128 chars)
        code_challenge — BASE64URL(SHA-256(code_verifier)), no padding
    """
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


# ---------------------------------------------------------------------------
# Local callback server (captures the authorization code)
# ---------------------------------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that captures the OAuth callback query parameters."""

    auth_code: Optional[str] = None
    error: Optional[str] = None
    _server_event: threading.Event = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != CANVA_REDIRECT_PATH:
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        _CallbackHandler.auth_code = (params.get("code") or [None])[0]
        _CallbackHandler.error = (params.get("error") or [None])[0]

        # Respond to the browser
        if _CallbackHandler.auth_code:
            body = (
                b"<html><body><h2>Canva authorization successful!</h2>"
                b"<p>You can close this tab and return to the terminal.</p></body></html>"
            )
        else:
            body = (
                b"<html><body><h2>Authorization failed.</h2>"
                b"<p>Check the terminal for details.</p></body></html>"
            )

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        # Signal the main thread
        _CallbackHandler._server_event.set()

    def log_message(self, format, *args):  # noqa: A002
        """Suppress default access log noise."""
        pass


def _run_local_auth_flow() -> dict:
    """
    Run the full PKCE OAuth 2.0 authorization flow:
      1. Generate PKCE pair.
      2. Build authorization URL and open it in the default browser.
      3. Spin up a local HTTP server on 127.0.0.1:8080 to catch the callback.
      4. Exchange the authorization code for tokens.

    Returns
    -------
    dict
        Token response from Canva (access_token, refresh_token, expires_in, …).

    Raises
    ------
    RuntimeError
        If the user denies access, the exchange fails, or required env vars are missing.
    """
    if not CANVA_CLIENT_ID:
        raise RuntimeError(
            "CANVA_CLIENT_ID is not set in .env. "
            "Add: CANVA_CLIENT_ID=OC-AZ4jhERgHtNN"
        )
    if not CANVA_CLIENT_SECRET:
        raise RuntimeError(
            "CANVA_CLIENT_SECRET is not set in .env. "
            "Generate one at: https://www.canva.com/developers/integrations/connect-api/"
            "OC-AZ4jhERgHtNN/authentication"
        )

    code_verifier, code_challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    # Reset handler state from any previous run in the same process
    _CallbackHandler.auth_code = None
    _CallbackHandler.error = None
    _CallbackHandler._server_event = threading.Event()

    # Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": CANVA_CLIENT_ID,
        "redirect_uri": CANVA_REDIRECT_URI,
        "scope": " ".join(CANVA_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{CANVA_AUTH_URL}?{urlencode(auth_params)}"

    logger.info("Starting local OAuth callback server on %s:%d", CANVA_REDIRECT_HOST, CANVA_REDIRECT_PORT)
    server = HTTPServer((CANVA_REDIRECT_HOST, CANVA_REDIRECT_PORT), _CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print(f"\nOpening Canva authorization URL in your browser:\n{auth_url}\n")
    print("If the browser doesn't open automatically, copy and paste the URL above.")
    webbrowser.open(auth_url)

    # Wait up to 5 minutes for the callback
    logger.info("Waiting for authorization callback …")
    _CallbackHandler._server_event.wait(timeout=300)
    server.server_close()

    if _CallbackHandler.error:
        raise RuntimeError(f"Canva authorization denied: {_CallbackHandler.error}")
    if not _CallbackHandler.auth_code:
        raise RuntimeError("No authorization code received — did the browser callback time out?")

    logger.info("Authorization code received. Exchanging for tokens …")

    # Token exchange — Canva requires HTTP Basic auth (client_id:client_secret)
    _basic = base64.b64encode(
        f"{CANVA_CLIENT_ID}:{CANVA_CLIENT_SECRET}".encode("ascii")
    ).decode("ascii")
    token_resp = requests.post(
        CANVA_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.auth_code,
            "redirect_uri": CANVA_REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {_basic}",
        },
        timeout=30,
    )

    if token_resp.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed: HTTP {token_resp.status_code} — {token_resp.text[:400]}"
        )

    token_data = token_resp.json()
    if "access_token" not in token_data:
        raise RuntimeError(f"Token response missing access_token: {token_resp.text[:400]}")

    # Record when this token was issued so we can detect expiry later
    token_data["issued_at"] = int(time.time())
    logger.info("Token exchange successful.")
    return token_data


def _refresh_access_token(refresh_token: str) -> dict:
    """
    Use the refresh_token to obtain a new access_token from Canva.

    Parameters
    ----------
    refresh_token:
        The refresh token from the stored canva_token.json.

    Returns
    -------
    dict
        Updated token dict (merged with existing data so refresh_token is preserved
        when Canva doesn't return a new one).

    Raises
    ------
    RuntimeError
        On any HTTP error or missing access_token.
    """
    logger.info("Refreshing Canva access token …")
    _basic = base64.b64encode(
        f"{CANVA_CLIENT_ID}:{CANVA_CLIENT_SECRET}".encode("ascii")
    ).decode("ascii")
    resp = requests.post(
        CANVA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {_basic}",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Token refresh failed: HTTP {resp.status_code} — {resp.text[:400]}"
        )

    new_data = resp.json()
    if "access_token" not in new_data:
        raise RuntimeError(f"Refresh response missing access_token: {resp.text[:400]}")

    new_data["issued_at"] = int(time.time())
    # Preserve existing refresh_token if Canva doesn't send a new one
    if "refresh_token" not in new_data:
        new_data["refresh_token"] = refresh_token
    logger.info("Token refreshed successfully.")
    return new_data


def _save_token(token_data: dict, token_path: str = CANVA_TOKEN_PATH) -> None:
    """Persist token_data as JSON to token_path."""
    with open(token_path, "w") as fh:
        json.dump(token_data, fh, indent=2)
    logger.info("Token saved to %s", token_path)


def _is_token_expired(token_data: dict, buffer_seconds: int = 60) -> bool:
    """
    Return True if the access token is expired (or will expire within buffer_seconds).

    Uses the 'issued_at' + 'expires_in' fields written by this module.
    Falls back to True (forces refresh) if either field is absent.
    """
    issued_at = token_data.get("issued_at")
    expires_in = token_data.get("expires_in")
    if issued_at is None or expires_in is None:
        return True
    expiry = issued_at + int(expires_in) - buffer_seconds
    return time.time() >= expiry


# ---------------------------------------------------------------------------
# Auth entry point (called by _headers() and CLI --auth)
# ---------------------------------------------------------------------------

# Module-level cache so we only load/refresh once per process
_cached_access_token: Optional[str] = None


def get_authenticated_client(token_path: str = CANVA_TOKEN_PATH) -> str:
    """
    Return a valid Canva OAuth 2.0 access token.

    Resolution order:
    1. Return the in-process cached token if already loaded this session.
    2. Load canva_token.json from disk; if the access token is still valid, use it.
    3. If the stored token is expired, refresh it with the refresh_token and save.
    4. If no token file exists, run the full PKCE browser flow and save the result.

    Parameters
    ----------
    token_path:
        Path to the JSON file where the token is stored.  Defaults to
        canva_token.json in the same directory as this script.

    Returns
    -------
    str
        A valid access token ready for use in Authorization headers.

    Raises
    ------
    RuntimeError
        If authentication fails for any reason.
    """
    global _cached_access_token

    from oauth_files import ensure_canva_token_file

    ensure_canva_token_file(Path(token_path))

    # Fast path: already authenticated this process
    if _cached_access_token:
        return _cached_access_token

    token_data: Optional[dict] = None

    # Load from disk
    if os.path.exists(token_path):
        try:
            with open(token_path) as fh:
                token_data = json.load(fh)
            logger.debug("Loaded token from %s", token_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s (%s) — will re-authenticate.", token_path, exc)
            token_data = None

    if token_data:
        if _is_token_expired(token_data):
            refresh_token = token_data.get("refresh_token")
            if refresh_token:
                token_data = _refresh_access_token(refresh_token)
                _save_token(token_data, token_path)
            else:
                logger.warning("Token is expired and no refresh_token found — re-authenticating.")
                token_data = None

    if not token_data:
        # Full PKCE flow
        token_data = _run_local_auth_flow()
        _save_token(token_data, token_path)

    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("No access_token found after authentication — something went wrong.")

    _cached_access_token = access_token
    return access_token


def get_access_token_if_available(
    token_path: str = CANVA_TOKEN_PATH,
    *,
    interactive: bool = False,
) -> str | None:
    """
    Return a valid Canva access token without launching browser OAuth.

    When ``interactive`` is False (default for pipeline folder resolution),
    returns None if no token file exists or refresh fails.
    """
    global _cached_access_token

    from oauth_files import ensure_canva_token_file

    ensure_canva_token_file(Path(token_path))

    if _cached_access_token:
        return _cached_access_token

    token_data: Optional[dict] = None

    if os.path.exists(token_path):
        try:
            with open(token_path) as fh:
                token_data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s (%s)", token_path, exc)
            token_data = None

    if token_data:
        try:
            if _is_token_expired(token_data):
                refresh_token = token_data.get("refresh_token")
                if refresh_token:
                    token_data = _refresh_access_token(refresh_token)
                    _save_token(token_data, token_path)
                else:
                    token_data = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Canva token refresh failed: %s", exc)
            token_data = None

    if not token_data:
        if interactive:
            return get_authenticated_client(token_path)
        return None

    access_token = token_data.get("access_token")
    if access_token:
        _cached_access_token = access_token
    return access_token


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _headers() -> dict[str, str]:
    """Return the Authorization header dict for every Canva API request."""
    token = get_authenticated_client()
    return {"Authorization": f"Bearer {token}"}


def _headers_silent() -> dict[str, str] | None:
    """Return auth headers only when a token is already available (no browser OAuth)."""
    token = get_access_token_if_available(interactive=False)
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _safe_title(text: str) -> str:
    """Convert an arbitrary string to a filesystem-safe slug."""
    return re.sub(r"[^\w\-]+", "_", text).strip("_")


def _list_folder_items(folder_id: str) -> list[dict]:
    """
    Return all items (designs + sub-folders) inside a Canva folder.

    Results are cached for the duration of the process so repeated calls
    within the same pipeline run don't re-hit the API.

    Parameters
    ----------
    folder_id:
        The Canva folder ID to enumerate.

    Returns
    -------
    list of raw item dicts from the Canva API (each has 'type' + 'design'/'folder' key).
    Raises RuntimeError on any HTTP error.
    """
    if folder_id in _folder_cache:
        logger.debug("Using cached folder listing for %s", folder_id)
        return _folder_cache[folder_id]

    items: list[dict] = []
    url = f"{CANVA_BASE_URL}/folders/{folder_id}/items"
    params: dict = {"item_types": "design", "sort_by": "modified_descending", "limit": 100}

    logger.info("Listing designs in Canva folder %s …", folder_id)
    while True:
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Canva API error listing folder {folder_id}: "
                f"HTTP {resp.status_code} — {resp.text[:300]}"
            )
        data = resp.json()
        items.extend(data.get("items", []))
        continuation = data.get("continuation")
        if not continuation:
            break
        params = {"continuation": continuation}

    logger.info("Found %d design(s) in folder %s", len(items), folder_id)
    _folder_cache[folder_id] = items
    return items


def _match_design(items: list[dict], meeting_title: str) -> dict | None:
    """
    Pick the best design from a folder item list.

    Strategy:
    1. Case-insensitive substring match on design title vs. meeting_title.
    2. If nothing matches, fall back to the first item (most recently modified,
       because the folder is sorted modified_descending).

    Returns the raw design dict (from item['design']), or None if items is empty.
    """
    needle = meeting_title.lower()

    for item in items:
        if item.get("type") != "design":
            continue
        design = item.get("design", {})
        title = design.get("title", "")
        if needle in title.lower():
            logger.info(
                "Matched design by title substring: '%s' (id=%s)", title, design.get("id")
            )
            return design

    # Fallback: most recently modified
    for item in items:
        if item.get("type") == "design":
            design = item["design"]
            logger.warning(
                "No title match for '%s'. Falling back to most-recently-modified design: "
                "'%s' (id=%s)",
                meeting_title,
                design.get("title"),
                design.get("id"),
            )
            return design

    logger.warning("Folder contains no design items — cannot match.")
    return None


def _export_design(design_id: str, poll_interval: float = 3.0, timeout: float = 60.0) -> list[str]:
    """
    Create a PNG export job and poll until it's done.

    Parameters
    ----------
    design_id:
        Canva design ID.
    poll_interval:
        Seconds between status-check polls.
    timeout:
        Maximum seconds to wait before giving up.

    Returns
    -------
    List of download URLs (one per page; YouTube thumbnails typically have one page).
    Raises RuntimeError on failure or timeout.
    """
    # POST /exports — create export job
    payload = {
        "design_id": design_id,
        "format": {
            "type": "png",
            "export_quality": "regular",
            "as_single_image": True,   # flatten multi-page into one image
            "pages": [1],              # thumbnail designs are single-page
        },
    }
    resp = requests.post(
        f"{CANVA_BASE_URL}/exports",
        headers={**_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Canva export creation failed (design_id={design_id}): "
            f"HTTP {resp.status_code} — {resp.text[:300]}"
        )

    job = resp.json().get("job", {})
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError(f"Canva export response missing job.id: {resp.text[:300]}")

    logger.info("Export job created: %s (design_id=%s). Polling …", job_id, design_id)

    # GET /exports/{exportId} — poll for completion
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        poll_resp = requests.get(
            f"{CANVA_BASE_URL}/exports/{job_id}",
            headers=_headers(),
            timeout=30,
        )
        if poll_resp.status_code != 200:
            raise RuntimeError(
                f"Canva export status check failed: "
                f"HTTP {poll_resp.status_code} — {poll_resp.text[:300]}"
            )

        poll_job = poll_resp.json().get("job", {})
        status = poll_job.get("status", "in_progress")
        logger.debug("Export job %s status: %s", job_id, status)

        if status == "success":
            urls = poll_job.get("urls", [])
            if not urls:
                raise RuntimeError(f"Export job {job_id} succeeded but returned no URLs.")
            logger.info("Export complete. Download URL count: %d", len(urls))
            return urls

        if status == "failed":
            err = poll_job.get("error", {})
            raise RuntimeError(
                f"Export job {job_id} failed: code={err.get('code')} "
                f"message={err.get('message')}"
            )

    raise TimeoutError(
        f"Export job {job_id} did not complete within {timeout:.0f}s."
    )


def _download_png(url: str, dest_path: str) -> str:
    """
    Download a PNG from url and save it to dest_path.

    Returns dest_path.
    """
    logger.info("Downloading exported PNG → %s", dest_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    resp = requests.get(url, timeout=60, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)
    size_kb = os.path.getsize(dest_path) / 1024
    logger.info("PNG saved (%.1f KB): %s", size_kb, dest_path)
    return dest_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_thumbnail(
    meeting_title: str,
    meeting_date: str | datetime,
    output_dir: str,
) -> str | None:
    """
    Fetch a Canva thumbnail matching meeting_title and export it as PNG.

    Parameters
    ----------
    meeting_title:
        The Zoom meeting topic / title. Used to search for a matching design.
    meeting_date:
        The recording date, used in the output filename.  Accepts a
        datetime object or an ISO-8601 date string (``YYYY-MM-DD`` or full
        datetime).
    output_dir:
        Directory where the PNG should be saved.

    Returns
    -------
    str
        Absolute path to the downloaded PNG file.
    None
        If anything goes wrong (missing config, API error, timeout, …).
        Callers should fall back to the YouTube auto-thumbnail.
    """
    # ------------------------------------------------------------------
    # Pre-flight checks
    # ------------------------------------------------------------------
    if not CANVA_CLIENT_ID:
        logger.warning("CANVA_CLIENT_ID is not set — skipping Canva thumbnail.")
        return None

    try:
        folder_id = resolve_thumbnail_folder_id()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Canva folder resolution failed — skipping thumbnail: %s", exc)
        return None

    if not folder_id:
        logger.warning(
            "No Canva thumbnail folder configured — skipping Canva thumbnail. "
            "Set CANVA_THUMBNAIL_FOLDER_NAME (default: Replay Thumbnail Folder) "
            "or CANVA_THUMBNAIL_FOLDER_ID. Run: python canva_thumbnail.py --list-folders"
        )
        return None

    # Normalise date to a YYYYMMDD string
    if isinstance(meeting_date, datetime):
        date_tag = meeting_date.strftime("%Y%m%d")
    else:
        # Accept "2025-05-01", "2025-05-01T18:00:00Z", etc.
        date_tag = str(meeting_date)[:10].replace("-", "")

    try:
        # Step 1 — list folder
        items = _list_folder_items(folder_id)

        # Step 2 — match design
        design = _match_design(items, meeting_title)
        if design is None:
            logger.warning("No designs found in folder — returning None.")
            return None

        design_id = design["id"]
        design_title = design.get("title", "thumbnail")

        # Step 3 — export
        urls = _export_design(design_id)
        png_url = urls[0]  # first (and usually only) page

        # Step 4 — download
        safe = _safe_title(design_title)
        filename = f"thumbnail_{safe}_{date_tag}.png"
        dest_path = os.path.join(output_dir, filename)
        return _download_png(png_url, dest_path)

    except Exception as exc:  # noqa: BLE001
        logger.error("Canva thumbnail failed: %s", exc, exc_info=True)
        return None


def resolve_thumbnail_folder_id() -> str | None:
    """
    Resolve the Canva folder ID for replay thumbnail templates.

    Prefers a folder name lookup (default: "Replay Thumbnail Folder") so the
    pipeline stays linked to the correct Canva folder even if the ID changes.
    Falls back to CANVA_THUMBNAIL_FOLDER_ID when name lookup is unavailable.
    """
    folder_name = CANVA_THUMBNAIL_FOLDER_NAME.strip()
    configured_id = CANVA_THUMBNAIL_FOLDER_ID.strip()

    if configured_id and not get_access_token_if_available(interactive=False):
        logger.info(
            "No Canva token yet — using CANVA_THUMBNAIL_FOLDER_ID without folder name lookup."
        )
        return configured_id

    if folder_name and CANVA_CLIENT_ID:
        try:
            resolved_id = get_folder_id_by_name(folder_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Canva folder name lookup failed: %s", exc)
            resolved_id = None
        if resolved_id:
            if configured_id and configured_id != resolved_id:
                logger.warning(
                    "CANVA_THUMBNAIL_FOLDER_ID (%s) does not match folder '%s' (%s); "
                    "using the folder resolved by name.",
                    configured_id,
                    folder_name,
                    resolved_id,
                )
            return resolved_id
        if configured_id:
            logger.warning(
                "Canva folder '%s' not found; falling back to CANVA_THUMBNAIL_FOLDER_ID.",
                folder_name,
            )
            return configured_id
        logger.warning("Canva folder '%s' not found and no folder ID configured.", folder_name)
        return None

    return configured_id or None


def get_folder_id_by_name(folder_name: str) -> str | None:
    """
    Search the root of the user's Canva projects for a folder whose name
    matches folder_name (case-insensitive exact match).

    Useful for discovering the folder ID before setting CANVA_THUMBNAIL_FOLDER_ID.

    Parameters
    ----------
    folder_name:
        The folder name to search for (e.g. "Guild Replay Thumbnails").

    Returns
    -------
    str
        The folder ID if found.
    None
        If not found or if CANVA_CLIENT_ID is not set.
    """
    if not CANVA_CLIENT_ID:
        logger.error("CANVA_CLIENT_ID is not set.")
        return None

    headers = _headers_silent()
    if headers is None:
        logger.warning(
            "No Canva token available for folder name lookup. "
            "Run: python canva_thumbnail.py --auth"
        )
        return None

    logger.info("Searching root folders for '%s' …", folder_name)
    url = f"{CANVA_BASE_URL}/folders/root/items"
    params: dict = {"item_types": "folder", "limit": 100}
    needle = folder_name.lower()

    while True:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            logger.error(
                "Canva API error listing root folders: HTTP %s — %s",
                resp.status_code,
                resp.text[:300],
            )
            return None

        data = resp.json()
        for item in data.get("items", []):
            if item.get("type") != "folder":
                continue
            folder = item.get("folder", {})
            if folder.get("name", "").lower() == needle:
                logger.info("Found folder '%s' with ID: %s", folder["name"], folder["id"])
                return folder["id"]

        continuation = data.get("continuation")
        if not continuation:
            break
        params = {"continuation": continuation}

    logger.warning("Folder '%s' not found in root projects.", folder_name)
    return None


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _cmd_auth() -> None:
    """Run the OAuth 2.0 PKCE auth flow and save canva_token.json."""
    print("Starting Canva OAuth 2.0 authorization flow …")
    try:
        # Clear in-process cache so we force a fresh flow
        global _cached_access_token
        _cached_access_token = None

        # Remove stale token file so get_authenticated_client runs the full flow
        if os.path.exists(CANVA_TOKEN_PATH):
            os.remove(CANVA_TOKEN_PATH)
            logger.info("Removed existing %s to start fresh.", CANVA_TOKEN_PATH)

        token = get_authenticated_client()
        print(f"\nAuthentication successful!")
        print(f"Token saved to: {CANVA_TOKEN_PATH}")
        print(f"Access token prefix: {token[:12]}…")
        print("\nNext step — find your thumbnail folder ID:")
        print("  python canva_thumbnail.py --list-folders")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_list_folders() -> None:
    """Print all top-level folders in the user's Canva projects."""
    if not CANVA_CLIENT_ID:
        print("ERROR: CANVA_CLIENT_ID is not set in .env", file=sys.stderr)
        sys.exit(1)

    url = f"{CANVA_BASE_URL}/folders/root/items"
    params: dict = {"item_types": "folder", "limit": 100}
    folders: list[dict] = []

    while True:
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            print(
                f"ERROR: HTTP {resp.status_code} — {resp.text[:300]}", file=sys.stderr
            )
            sys.exit(1)
        data = resp.json()
        for item in data.get("items", []):
            if item.get("type") == "folder":
                folders.append(item["folder"])
        continuation = data.get("continuation")
        if not continuation:
            break
        params = {"continuation": continuation}

    if not folders:
        print("No folders found at the root of your Canva projects.")
        return

    print(f"\n{'ID':<30}  {'Name'}")
    print("-" * 70)
    for f in sorted(folders, key=lambda x: x.get("name", "").lower()):
        print(f"{f['id']:<30}  {f['name']}")
    print(
        "\nSet the replay thumbnail folder in .env as either:\n"
        f"  CANVA_THUMBNAIL_FOLDER_NAME={CANVA_THUMBNAIL_FOLDER_NAME!r}\n"
        "  CANVA_THUMBNAIL_FOLDER_ID=<id>   (optional fallback)"
    )


def _cmd_list_folder() -> None:
    """Print all designs in the configured replay thumbnail folder."""
    if not CANVA_CLIENT_ID:
        print("ERROR: CANVA_CLIENT_ID is not set in .env", file=sys.stderr)
        sys.exit(1)

    folder_id = resolve_thumbnail_folder_id()
    if not folder_id:
        print(
            "ERROR: Could not resolve thumbnail folder. "
            f"Set CANVA_THUMBNAIL_FOLDER_NAME (default: {CANVA_THUMBNAIL_FOLDER_NAME!r}) "
            "or CANVA_THUMBNAIL_FOLDER_ID, then run --list-folders.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using folder ID: {folder_id}")
    try:
        items = _list_folder_items(folder_id)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    designs = [i["design"] for i in items if i.get("type") == "design"]
    if not designs:
        print("No designs found in the configured folder.")
        return

    print(f"\n{'ID':<30}  {'Updated':<12}  {'Title'}")
    print("-" * 80)
    for d in designs:
        updated = datetime.utcfromtimestamp(d.get("updated_at", 0)).strftime("%Y-%m-%d")
        print(f"{d['id']:<30}  {updated:<12}  {d.get('title', '(no title)')}")


def _cmd_match(meeting_title: str) -> None:
    """Test the match logic against the configured folder."""
    if not CANVA_CLIENT_ID:
        print("ERROR: CANVA_CLIENT_ID is not set in .env", file=sys.stderr)
        sys.exit(1)

    folder_id = resolve_thumbnail_folder_id()
    if not folder_id:
        print(
            "ERROR: Could not resolve thumbnail folder. "
            f"Set CANVA_THUMBNAIL_FOLDER_NAME (default: {CANVA_THUMBNAIL_FOLDER_NAME!r}) "
            "or CANVA_THUMBNAIL_FOLDER_ID.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using folder ID: {folder_id}")
    try:
        items = _list_folder_items(folder_id)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    design = _match_design(items, meeting_title)
    if design:
        print(f"\nMatched design:")
        print(f"  ID    : {design.get('id')}")
        print(f"  Title : {design.get('title')}")
        updated = datetime.utcfromtimestamp(design.get("updated_at", 0)).strftime("%Y-%m-%d")
        print(f"  Updated: {updated}")
    else:
        print("No design matched (folder may be empty).")


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Canva thumbnail helper for Ganjier Guild replay pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--auth",
        action="store_true",
        help="Run the OAuth 2.0 authorization flow and save canva_token.json (use this for first-time setup).",
    )
    group.add_argument(
        "--list-folders",
        action="store_true",
        help="List all top-level folders in your Canva projects (use this to find CANVA_THUMBNAIL_FOLDER_ID).",
    )
    group.add_argument(
        "--list-folder",
        action="store_true",
        help="List all designs in the folder set by CANVA_THUMBNAIL_FOLDER_ID.",
    )
    group.add_argument(
        "--match",
        metavar="MEETING_TITLE",
        help="Test design matching for the given meeting title against CANVA_THUMBNAIL_FOLDER_ID.",
    )
    args = parser.parse_args()

    if args.auth:
        _cmd_auth()
    elif args.list_folders:
        _cmd_list_folders()
    elif args.list_folder:
        _cmd_list_folder()
    elif args.match:
        _cmd_match(args.match)


if __name__ == "__main__":
    main()
