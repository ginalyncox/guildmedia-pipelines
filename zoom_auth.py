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

ACCOUNT_ENV_PREFIXES = {
    "jward": "ZOOM_JWARD",
    "navigators": "ZOOM_NAVIGATORS",
}


class ZoomAuthError(Exception):
    """Raised when Zoom authentication fails."""

    def __init__(self, account_name: str, message: str) -> None:
        self.account_name = account_name
        super().__init__(f"[{account_name}] {message}")


class ZoomAuth:
    """Manages a Zoom Server-to-Server OAuth token for a single account."""

    def __init__(
        self,
        name: str,
        account_id: str,
        client_id: str,
        client_secret: str,
        *,
        static_token: bool = False,
    ) -> None:
        self.name = name
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._static_token = static_token
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def _fetch_token(self) -> tuple[str, float]:
        if not all([self.account_id, self.client_id, self.client_secret]):
            raise ZoomAuthError(
                self.name,
                "account_id, client_id, and client_secret must all be set in .env",
            )

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode("utf-8")
        ).decode("utf-8")

        resp = requests.post(
            "https://zoom.us/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Host": "zoom.us",
            },
            data={
                "grant_type": "account_credentials",
                "account_id": self.account_id,
            },
            timeout=30,
        )

        if not resp.ok:
            detail = resp.text[:300]
            try:
                payload = resp.json()
                reason = payload.get("reason") or payload.get("error") or detail
            except ValueError:
                reason = detail
            raise ZoomAuthError(
                self.name,
                f"OAuth token request failed (HTTP {resp.status_code}): {reason}",
            )

        data = resp.json()
        access_token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not access_token:
            raise ZoomAuthError(self.name, f"Token response missing access_token: {data}")

        return access_token, time.monotonic() + expires_in - 60

    def get_token(self) -> str:
        if self._static_token:
            if self._access_token is None:
                raise ZoomAuthError(self.name, "static access token is not set")
            return self._access_token
        if self._access_token is None or time.monotonic() >= self._expires_at:
            self._access_token, self._expires_at = self._fetch_token()
        return self._access_token

    def invalidate(self) -> None:
        if self._static_token:
            return
        self._access_token = None
        self._expires_at = 0.0


def _build_account_auth(name: str, env_prefix: str) -> ZoomAuth | None:
    account_id = os.getenv(f"{env_prefix}_ACCOUNT_ID", "").strip()
    client_id = os.getenv(f"{env_prefix}_CLIENT_ID", "").strip()
    client_secret = os.getenv(f"{env_prefix}_CLIENT_SECRET", "").strip()
    access_token = os.getenv(f"{env_prefix}_ACCESS_TOKEN", "").strip()

    if access_token:
        auth = ZoomAuth(name, account_id, client_id, client_secret, static_token=True)
        auth._access_token = access_token
        auth._expires_at = float("inf")
        return auth

    if account_id and client_id and client_secret:
        return ZoomAuth(name, account_id, client_id, client_secret)

    return None


def auth_status(auth: ZoomAuth) -> tuple[bool, str]:
    """Return (ok, message) for an account's current auth configuration."""
    try:
        auth.get_token()
        mode = "access token" if auth._static_token else "Server-to-Server OAuth"
        return True, f"{mode} OK"
    except ZoomAuthError as exc:
        auth.invalidate()
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        auth.invalidate()
        return False, str(exc)


def verify_auth(auth: ZoomAuth) -> bool:
    """Return True when the account can obtain a Zoom access token."""
    ok, _ = auth_status(auth)
    return ok


def configured_accounts(*, only_working: bool = False) -> list[ZoomAuth]:
    """Return ZoomAuth instances for every configured account."""
    accounts = [
        auth
        for name, prefix in ACCOUNT_ENV_PREFIXES.items()
        if (auth := _build_account_auth(name, prefix)) is not None
    ]

    if only_working:
        accounts = [auth for auth in accounts if verify_auth(auth)]

    return accounts


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
            legacy = ZoomAuth("legacy", "", "", "", static_token=True)
            legacy._access_token = legacy_token
            legacy._expires_at = float("inf")
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
        try:
            token = auth.get_token()
        except ZoomAuthError as exc:
            last_error = exc
            continue

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
        "No Zoom credentials configured. Set ZOOM_JWARD_* / ZOOM_NAVIGATORS_* "
        "or per-account ZOOM_*_ACCESS_TOKEN values."
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
