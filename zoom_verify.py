"""
zoom_verify.py — Validate Zoom account credentials from .env.

Usage:
    python3 zoom_verify.py
    python3 zoom_verify.py --account jward
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from zoom_auth import ACCOUNT_ENV_PREFIXES, _build_account_auth, auth_status, zoom_api_get

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Zoom OAuth credentials in .env")
    parser.add_argument(
        "--account",
        choices=list(ACCOUNT_ENV_PREFIXES),
        help="Only test one account (default: test all configured accounts)",
    )
    args = parser.parse_args()

    names = [args.account] if args.account else list(ACCOUNT_ENV_PREFIXES)
    failures = 0

    for name in names:
        prefix = ACCOUNT_ENV_PREFIXES[name]
        auth = _build_account_auth(name, prefix)
        if auth is None:
            print(f"[{name}] NOT CONFIGURED — set {prefix}_ACCOUNT_ID, *_CLIENT_ID, *_CLIENT_SECRET")
            failures += 1
            continue

        ok, message = auth_status(auth)
        if ok:
            print(f"[{name}] OK — {message}")
            try:
                data = zoom_api_get(
                    "/v2/users/me/recordings",
                    auth,
                    {"from": "2024-01-01", "to": "2026-12-31", "page_size": 1},
                )
                count = len(data.get("meetings", []))
                print(f"[{name}] recordings API reachable (sample page: {count} meeting(s))")
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] WARN — token works but recordings API failed: {exc}")
        else:
            print(f"[{name}] FAIL — {message}")
            if "invalid client" in message.lower() or "invalid_client" in message.lower():
                print(
                    f"[{name}] FIX — open the {name} Server-to-Server OAuth app in the "
                    "Zoom Marketplace, copy a fresh Account ID / Client ID / Client Secret, "
                    f"and update {prefix}_* in your GitHub .env."
                )
            failures += 1

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
