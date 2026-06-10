"""Materialize OAuth token/secret files from .env when running in cloud environments."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")


def _write_if_missing(path: Path, content: str) -> bool:
    if path.exists() or not content.strip():
        return path.exists()
    path.write_text(content.strip(), encoding="utf-8")
    return True


def ensure_youtube_oauth_files(
    token_path: Path | None = None,
    secrets_path: Path | None = None,
) -> None:
    """Create token.json / client_secrets.json from .env when absent."""
    token_path = token_path or (SCRIPT_DIR / "token.json")
    secrets_path = secrets_path or (SCRIPT_DIR / "client_secrets.json")

    _write_if_missing(token_path, os.getenv("YOUTUBE_TOKEN_JSON", ""))

    secrets_json = os.getenv("GOOGLE_CLIENT_SECRETS_JSON", "").strip()
    if not secrets_json:
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        if client_id and client_secret:
            secrets_json = json.dumps(
                {
                    "installed": {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": ["http://localhost"],
                    }
                }
            )

    _write_if_missing(secrets_path, secrets_json)


def ensure_canva_token_file(token_path: Path | None = None) -> None:
    """Create canva_token.json from .env when absent."""
    token_path = token_path or (SCRIPT_DIR / "canva_token.json")
    _write_if_missing(token_path, os.getenv("CANVA_TOKEN_JSON", ""))


def youtube_files_ready() -> bool:
    ensure_youtube_oauth_files()
    return (SCRIPT_DIR / "token.json").exists() and (SCRIPT_DIR / "client_secrets.json").exists()


def canva_token_ready() -> bool:
    ensure_canva_token_file()
    return (SCRIPT_DIR / "canva_token.json").exists()


def ensure_service_account_file(path: Path | None = None) -> None:
    """Create service_account.json from .env when absent (Google Sheets tracker)."""
    path = path or (SCRIPT_DIR / "service_account.json")
    _write_if_missing(path, os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""))


def service_account_ready() -> bool:
    ensure_service_account_file()
    if (SCRIPT_DIR / "service_account.json").exists():
        return True
    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    return bool(file_path and Path(file_path).exists())
