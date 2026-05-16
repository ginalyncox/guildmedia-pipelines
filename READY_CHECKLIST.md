# Replay Pipeline — Ready Checklist
Last updated: 2026-05-13

## ✅ Done
- [x] All 6 scripts written and verified
- [x] Dual Zoom account support (jward + navigators)
- [x] Canva OAuth 2.0 with PKCE (replaces API key approach)
- [x] WP_REPLAY_CPT configurable (replay = prod, gc_replay = staging)
- [x] .env.example pre-filled with all non-secret values
- [x] .gitignore covers .env, token.json, canva_token.json, client_secrets.json
- [x] Zoom jward credentials captured
- [x] Zoom navigators credentials captured
- [x] YouTube channel confirmed (UCsaeMxhYi2cpqvzfOBWTDaQ)
- [x] Canva integration created (OC-AZ4jhERgHtNN), redirect URL set

## ⏳ Remaining Blockers (4 items)

### 1. client_secrets.json
- Delete "Replay Pipeline" OAuth client at:
  https://console.cloud.google.com/auth/clients?authuser=2&project=crested-primacy-496221-f5
- Recreate as Desktop app → click Download JSON immediately on the confirmation screen
- Rename to client_secrets.json → place in zoom_pipeline/

### 2. ZOOM_WEBHOOK_SECRET_TOKEN
- Go to: https://marketplace.zoom.us
- Open your Webhook Only app → Feature → Event Subscriptions
- Copy the Secret Token → paste into .env

### 3. WP_APP_PASSWORD (fresh)
- WP Admin → Users → your profile → Application Passwords
- Revoke old "Replay Pipeline" password
- Add New → name it "Replay Pipeline" → copy the generated value → paste into .env

### 4. CANVA_THUMBNAIL_FOLDER_ID
- After completing client_secrets.json step, run:
  python canva_thumbnail.py --auth
  python canva_thumbnail.py --list-folders
- Copy the ID for your thumbnail folder → paste into .env

---

## First Run Order (once all 4 blockers are cleared)

```bash
# Step 1 — YouTube one-time browser auth
python upload_youtube.py --test-auth

# Step 2 — Canva one-time browser auth + get folder ID
python canva_thumbnail.py --auth
python canva_thumbnail.py --list-folders

# Step 3 — Preview backfill (both Zoom accounts, no changes made)
python backfill.py --dry-run

# Step 4 — Run backfill (resumable — safe to interrupt)
python backfill.py

# Step 5 — Start webhook listener for all future recordings
python pipeline.py --webhook
```

---

## .env Quick Reference (fill in blanks locally)

```
ZOOM_JWARD_ACCOUNT_ID=ozXPySCRQnW92mKd_AShsg
ZOOM_JWARD_CLIENT_ID=lcOk4NTD2zQxltnJ4Rkw
ZOOM_JWARD_CLIENT_SECRET=GhiFiNrvM8HBGpEDdUwzCDyWmDn2ZoqM

ZOOM_NAVIGATORS_ACCOUNT_ID=UHZIXq4nQ3az3UUaGbVS_g
ZOOM_NAVIGATORS_CLIENT_ID=V61bHTVQTN6obqgfsHKvKQ
ZOOM_NAVIGATORS_CLIENT_SECRET=OXWnDCASnmctIEURI5aTWySl2LNvssm5

ZOOM_WEBHOOK_SECRET_TOKEN=          ← still needed

YOUTUBE_PLAYLIST_NAME=Replays
YOUTUBE_CHANNEL_ID=UCsaeMxhYi2cpqvzfOBWTDaQ

WP_BASE_URL=https://ganjierguild.com
WP_USER=gina.cox
WP_APP_PASSWORD=                    ← fresh one needed
WP_REPLAY_CPT=replay

CANVA_CLIENT_ID=OC-AZ4jhERgHtNN
CANVA_CLIENT_SECRET=cnvcaOfYK743t-WHG1rG0RO9Icv0B4pzBEEVYkMf44Dad85sbfbaafef
CANVA_REDIRECT_URI=http://127.0.0.1:8080/canva/callback
CANVA_THUMBNAIL_FOLDER_ID=          ← after --list-folders

BACKFILL_FROM_DATE=2023-01-01
BACKFILL_TOPIC_FILTER=
BACKFILL_DELAY_SECONDS=5

TEMP_DIR=/tmp/zoom_pipeline
```
