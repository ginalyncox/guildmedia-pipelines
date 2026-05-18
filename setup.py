"""
setup.py -- Ganjier Guild Replay Pipeline
Writes a fully pre-filled .env file next to this script.
Run with:  py setup.py
"""
import os
import sys

# Always write .env next to THIS file, regardless of where you run from
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")

ENV_CONTENT = """\
# --- Zoom: jward account ---
ZOOM_JWARD_ACCOUNT_ID=your_jward_account_id
ZOOM_JWARD_CLIENT_ID=your_jward_client_id
ZOOM_JWARD_CLIENT_SECRET=your_jward_client_secret

# --- Zoom: navigators account ---
ZOOM_NAVIGATORS_ACCOUNT_ID=your_navigators_account_id
ZOOM_NAVIGATORS_CLIENT_ID=your_navigators_client_id
ZOOM_NAVIGATORS_CLIENT_SECRET=your_navigators_client_secret

# --- Zoom: webhook secrets ---
ZOOM_JWARD_WEBHOOK_SECRET=your_jward_webhook_secret
ZOOM_NAVIGATORS_WEBHOOK_SECRET=your_navigators_webhook_secret

# --- YouTube ---
YOUTUBE_PLAYLIST_NAME=Replays
YOUTUBE_CHANNEL_ID=UCsaeMxhYi2cpqvzfOBWTDaQ

# --- WordPress ---
WP_BASE_URL=https://ganjierguild.com
WP_USER=gina.cox
WP_APP_PASSWORD=Z4Yt UEq8 xDmR VUbk IXrw dWrv
WP_REPLAY_CPT=replay

# --- Canva ---
CANVA_CLIENT_ID=OC-AZ4jhERgHtNN
CANVA_CLIENT_SECRET=cnvcaOfYK743t-WHG1rG0RO9Icv0B4pzBEEVYkMf44Dad85sbfbaafef
CANVA_REDIRECT_URI=http://127.0.0.1:8080/canva/callback
CANVA_THUMBNAIL_FOLDER_ID=FAHEEnvFnlM

# --- Backfill ---
BACKFILL_FROM_DATE=2023-01-01
BACKFILL_TOPIC_FILTER=
BACKFILL_DELAY_SECONDS=5

# --- Pipeline ---
TEMP_DIR=%TEMP%\\zoom_pipeline
"""

def main():
    print(f"Writing .env to: {ENV_PATH}")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write(ENV_CONTENT)

    print(f".env written successfully.")

    # Auto-copy client_secrets.json from Downloads if not already present
    secrets_dest = os.path.join(SCRIPT_DIR, "client_secrets.json")
    if not os.path.exists(secrets_dest):
        downloads = os.path.join(os.path.expanduser("~"), "Downloads", "client_secrets.json")
        if os.path.exists(downloads):
            import shutil
            shutil.copy(downloads, secrets_dest)
            print(f"Copied client_secrets.json from Downloads.")
        else:
            print(f"WARNING: client_secrets.json not found in Downloads — copy it manually.")
    else:
        print(f"client_secrets.json already present.")

    print()
    print("Next steps:")
    print("  1. py upload_youtube.py --test-auth   (YouTube — already done)")
    print("  2. py canva_thumbnail.py --auth        (Canva — run this next)")
    print("  3. py backfill.py --dry-run            (review recordings)")
    print("  4. py backfill.py                      (real backfill)")
    print("  5. py pipeline.py --webhook            (live listener)")


if __name__ == "__main__":
    main()
