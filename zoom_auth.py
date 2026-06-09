"""
zoom_auth.py — Shared Zoom Server-to-Server OAuth helpers.

Used by pipeline.py, backfill.py, and poll_zoom.py.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Iterator

import requests
from dotenv import load_dotenv

load_dotenv()


class ZoomAuth:
    """Manages a Zoom Server-to-Server OAuth token for a single account."""

    def __init__(self, name: str, account_id: str, client_id: str, client_secret: str) -> None:
        self.name = name
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def _fetch_token(self) -> tuple[str, float]:
        if not all([self.account_id, self.client_id, self.client_secret]):
            raise RuntimeError(
                f"[{self.name}] account_id, client_id, and client_secret must all be set in .env"
            )

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("utf-8")

        url = (
            "https://zoom.us/oauth/token"
            f"?grant_type=account_credentials&account_id={self.account_id}"
        )
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        access_token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not access_token:
            raise RuntimeError(f"[{self.name}] Token response missing access_token: {data}")

        return access_token, time.monotonic() + expires_in - 60

    def get_token(self) -> str:
        if self._access_token is None or time.monotonic() >= self._expires_at:
            self._access_token, self._expires_at = self._fetch_token()
        return self._access_token

    def invalidate(self) -> None:
        self._access_token = None
        self._expires_at = 0.0


def configured_accounts() -> list[ZoomAuth]:
    """Return ZoomAuth instances for every account with complete credentials."""
    accounts = [
        ZoomAuth(
            name="jward",
            account_id=os.getenv("ZOOM_JWARD_ACCOUNT_ID", ""),
            client_id=os.getenv("ZOOM_JWARD_CLIENT_ID", ""),
            client_secret=os.getenv("ZOOM_JWARD_CLIENT_SECRET", ""),
        ),
        ZoomAuth(
            name="navigators",
            account_id=os.getenv("ZOOM_NAVIGATORS_ACCOUNT_ID", ""),
            client_id=os.getenv("ZOOM_NAVIGATORS_CLIENT_ID", ""),
            client_secret=os.getenv("ZOOM_NAVIGATORS_CLIENT_SECRET", ""),
        ),
    ]
    return [
        auth
        for auth in accounts
        if auth.account_id and auth.client_id and auth.client_secret
    ]


def auth_for_account_id(account_id: str) -> ZoomAuth | None:
    """Return the configured auth object matching a Zoom account_id."""
    for auth in configured_accounts():
        if auth.account_id == account_id:
            return auth
    return None


def iter_download_auths(account_id: str | None = None) -> Iterator[ZoomAuth]:
    """
    Yield auth objects to try for a recording download.

    When account_id is known, that account is tried first. Remaining configured
    accounts are yielded as fallbacks.
    """
    auths = configured_accounts()
    if not auths:
        legacy_token = os.getenv("ZOOM_ACCESS_TOKEN", "").strip()
        if legacy_token:
            legacy = ZoomAuth("legacy", "legacy", "legacy", "legacy")
            legacy._access_token = legacy_token
            legacy._expires_at = time.monotonic() + 3600
            yield legacy
        return

    primary = auth_for_account_id(account_id) if account_id else None
    if primary:
        yield primary

    for auth in auths:
        if primary is None or auth.account_id != primary.account_id:
            yield auth


def download_recording(download_url: str, dest_path: str, account_id: str | None = None) -> str:
    """
    Download a Zoom recording MP4 using Server-to-Server OAuth.

    Tries the matching account first, then other configured accounts. Falls back
    to ZOOM_ACCESS_TOKEN when no S2S credentials are configured.
    """
    last_error: Exception | None = None

    for auth in iter_download_auths(account_id):
        token = auth.get_token()
        try:
            with requests.get(
                download_url,
                headers={"Authorization": f"Bearer {token}"},
                stream=True,
                timeout=300,
            ) as resp:
                if resp.status_code == 401:
                    auth.invalidate()
                    last_error = RuntimeError(
                        f"[{auth.name}] Download unauthorized (HTTP 401)"
                    )
                    continue
                if resp.status_code != 200:
                    last_error = RuntimeError(
                        f"[{auth.name}] Download failed: HTTP {resp.status_code} "
                        f"— {resp.text[:200]}"
                    )
                    continue

                os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
                with open(dest_path, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        fh.write(chunk)
            return dest_path
        except requests.RequestException as exc:
            last_error = exc

    if last_error:
        raise RuntimeError(f"All Zoom download attempts failed: {last_error}") from last_error
    raise RuntimeError(
        "No Zoom credentials configured. Set ZOOM_JWARD_* / ZOOM_NAVIGATORS_* or ZOOM_ACCESS_TOKEN."
    )


def zoom_api_get(
    path: str,
    auth: ZoomAuth,
    params: dict | None = None,
    *,
    retries: int = 5,
) -> dict:
    """Authenticated GET to the Zoom API with 401/429 retry handling."""
    url = f"https://api.zoom.us{path}"
    wait = 2.0

    for attempt in range(1, retries + 1):
        token = auth.get_token()
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=60,
        )

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 401:
            auth.invalidate()
            time.sleep(wait)
            wait = min(wait * 2, 60)
            continue

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", str(int(wait))))
            time.sleep(retry_after)
            wait = min(wait * 2, 120)
            continue

        raise RuntimeError(
            f"[{auth.name}] Zoom API {path} returned HTTP {resp.status_code}: "
            f"{resp.text[:300]}"
        )

    raise RuntimeError(f"[{auth.name}] Zoom API {path} failed after {retries} attempts.")
