# Zoom Replay Workflow — Operator Instructions

This document explains how the replay pipeline works and what you need to do before,
during, and after each recording.

---

## End-to-end flow

```
Zoom recording ends
  → webhook hits pipeline.py (port 5055, /zoom/webhook)
  → download MP4 from Zoom Cloud
  → trim pre/post-roll with ffmpeg
  → prepend branded YouTube intro (optional)
  → upload to YouTube (unlisted, added to Replays playlist)
  → fetch Canva thumbnail (optional)
  → create WordPress replay post on ganjierguild.com
  → match + link the MEC calendar event (when a scheduled event matches)
  → log run to WordPress dashboard (Tools → Replay Pipeline)
  → clean up temp files
```

If the webhook is missed, `poll_zoom.py` (cron every 30 min) or `backfill.py`
catches recordings later.

---

## Before a recording

### 1. Confirm the webhook listener is running (production)

On Cloudways / the production server:

```bash
python3 pipeline.py --webhook
```

Keep this running under systemd, supervisor, or tmux. The WordPress webhook bridge
must forward to `http://127.0.0.1:5055/zoom/webhook`.

### 2. Prepare the Canva thumbnail (recommended)

Thumbnails are **not generated** by the pipeline. They are pre-made designs
exported from your Canva folder.

**Folder:** `Replay Thumbnail Folder` (resolved by name via
`CANVA_THUMBNAIL_FOLDER_NAME` in `.env`)

**For each meeting, create or update a Canva design in that folder whose title
contains the Zoom meeting topic.** Examples:

| Zoom meeting topic | Canva design title (will match) |
|---|---|
| `Guild Monthly Webinar` | `Guild Monthly Webinar — June 2026` |
| `Navigator Session` | `Navigator Session Thumbnail` |

The pipeline matches with a **case-insensitive substring search** on the design
title. If nothing matches, it falls back to the **most recently modified**
design in the folder — so keep unrelated designs out of that folder.

**Verify before go-live:**

```bash
python3 canva_thumbnail.py --list-folder
python3 canva_thumbnail.py --match "Your Exact Zoom Meeting Topic"
```

### 3. Name the Zoom meeting clearly (and align MEC + Canva)

The Zoom **meeting topic** becomes:

- The YouTube video title (with date appended)
- The WordPress replay post title
- The search string for Canva thumbnail matching
- The primary key for **MEC calendar event** matching

Use a consistent, descriptive topic string. The MEC event title must contain the
same session name (e.g. Zoom topic `All Hands On Deck` → MEC title
`Ganjier Guild – All Hands On Deck`).

**Full standard:** [`MEC_EVENT_STANDARD.md`](MEC_EVENT_STANDARD.md)  
**Copy-paste per series:** [`MEC_SERIES_TEMPLATES.md`](MEC_SERIES_TEMPLATES.md)

---

## During / after a recording

Nothing manual is required if all credentials are configured. Zoom fires
`recording.completed` → the pipeline runs automatically in the background.

Watch logs:

```bash
tail -f logs/pipeline.log
```

---

## If something is missed

### Preview what would be processed

```bash
python3 backfill.py --dry-run
python3 backfill.py --account jward --dry-run
python3 backfill.py --account navigators --dry-run
```

### Process missed recordings

```bash
python3 backfill.py
```

### Poll for recent recordings (cron fallback)

```bash
python3 poll_zoom.py
```

Cron example (every 30 minutes):

```cron
*/30 * * * * /usr/bin/python3 /path/to/poll_zoom.py >> /var/log/zoom-poll.log 2>&1
```

---

## YouTube intro (optional)

Each replay can open with a short branded intro before the meeting content.

### Preview the default intro

```bash
python3 replay_intro.py build --title "Guild Monthly Webinar" --output /tmp/intro_preview.mp4
```

This generates a 5-second slate (dark green background, Ganjier Guild branding,
meeting title) using ffmpeg — no Canva or video editor required.

### Enable in the pipeline

After you approve the look, set in `.env`:

```env
REPLAY_INTRO_ENABLED=true
REPLAY_INTRO_DURATION=5
REPLAY_INTRO_DYNAMIC_TITLE=true
```

`REPLAY_INTRO_DYNAMIC_TITLE=true` generates a fresh intro per meeting with the
Zoom topic on screen. Set it to `false` to reuse a single static file.

### Use a custom intro from Canva

1. Export a 1920×1080 MP4 from Canva (5–8 seconds, with audio or silent).
2. Save it as `assets/custom_intro.mp4` (or any path).
3. Set:
   ```env
   REPLAY_INTRO_ENABLED=true
   REPLAY_INTRO_PATH=assets/custom_intro.mp4
   REPLAY_INTRO_DYNAMIC_TITLE=false
   ```

### Test on a trimmed file

```bash
python3 replay_intro.py prepend trimmed.mp4 --title "Meeting Topic" -o /tmp/with_intro.mp4
```

---

## Canva thumbnail workflow (detailed)

### Setup (one time)

1. Authenticate with Canva:
   ```bash
   python3 canva_thumbnail.py --auth
   ```
2. Confirm your folder exists:
   ```bash
   python3 canva_thumbnail.py --list-folders
   ```
3. Set in `.env`:
   ```env
   CANVA_THUMBNAIL_FOLDER_NAME=Replay Thumbnail Folder
   # CANVA_THUMBNAIL_FOLDER_ID=<id>   # optional fallback
   ```

### Per-meeting checklist

- [ ] Design exists in **Replay Thumbnail Folder**
- [ ] Design title **contains** the Zoom meeting topic
- [ ] Design is single-page (exported as one PNG)
- [ ] Run `--match` to confirm the right design is selected

### If Canva is skipped

When Canva is not configured, auth fails, or no design matches, the pipeline
uses the **YouTube auto-generated thumbnail** for the WordPress featured image.
The replay post is still created.

---

## MEC calendar event linking

Scheduled sessions on ganjierguild.com use **Modern Events Calendar** (`mec-events`).
After each replay is published, the pipeline:

1. Finds the MEC event on the recording date whose title matches the Zoom topic
2. Stores replay + YouTube links on that event
3. Shows a **Replay available** notice on the MEC event page

Requires the updated **Ganjier Replay Pipeline** plugin (v1.1+).

```env
MEC_LINK_ENABLED=true
MEC_MATCH_MIN_SCORE=40
```

Matching uses the site timezone and checks the recording date ±1 day for recurring
events like *All Hands On Deck*.

**Operator standard:** [`MEC_EVENT_STANDARD.md`](MEC_EVENT_STANDARD.md) · **Series copy-paste blocks:** [`MEC_SERIES_TEMPLATES.md`](MEC_SERIES_TEMPLATES.md)

---

## Replay Pipeline dashboard (WordPress plugin)

The tracker lives in WordPress — not a separate spreadsheet. Install the plugin
from `wordpress-plugin/ganjier-replay-pipeline/` and view it at **Tools → Replay Pipeline**.

### One-time setup

1. Zip and upload the plugin folder via WordPress Admin → Plugins → Add New.
2. Activate **Ganjier Replay Pipeline**.
3. Deactivate the old **Zoom Webhook Bridge** plugin if present (the new plugin includes it).
4. Point Zoom webhooks at `https://ganjierguild.com/wp-json/gg/v1/zoom-webhook`.

The Python pipeline logs each run automatically using existing `WP_USER` /
`WP_APP_PASSWORD` credentials. No extra secrets are required.

### Verify

```bash
python3 replay_tracker.py --test
```

### Optional Google Sheets mirror (migration only)

To keep the Shared Drive sheet in sync during migration, set
`REPLAY_TRACKER_BACKEND=both` and configure `GOOGLE_SHEETS_SPREADSHEET_ID` plus a
service account. See `.env.example`.

---

## Diagnostics

```bash
python3 troubleshoot.py       # full environment check
python3 zoom_verify.py        # test Zoom OAuth per account
python3 -m unittest tests.test_pipeline_wiring
```

| Check | Command |
|---|---|
| Zoom accounts | `python3 zoom_verify.py` |
| Canva folder + designs | `python3 canva_thumbnail.py --list-folder` |
| Canva title match | `python3 canva_thumbnail.py --match "Topic"` |
| WordPress auth | see `AGENTS.md` |
| Webhook alive | `curl` validation test in `AGENTS.md` |

---

## Credential quick reference

| Service | `.env` keys | One-time files |
|---|---|---|
| Zoom (per account) | `ZOOM_JWARD_*`, `ZOOM_NAVIGATORS_*` | — |
| Zoom webhooks | `ZOOM_JWARD_WEBHOOK_SECRET`, `ZOOM_NAVIGATORS_WEBHOOK_SECRET` | — |
| YouTube | `YOUTUBE_PLAYLIST_NAME` | `client_secrets.json`, `token.json` |
| WordPress | `WP_BASE_URL`, `WP_USER`, `WP_APP_PASSWORD`, `WP_REPLAY_CPT` | — |
| Canva | `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_THUMBNAIL_FOLDER_NAME` | `canva_token.json` |
| Replay tracker | `REPLAY_TRACKER_BACKEND`, `WP_*` (WordPress default) | `wordpress-plugin/ganjier-replay-pipeline/` plugin |
| Sheets mirror (optional) | `GOOGLE_SHEETS_SPREADSHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` | `service_account.json` |

See `SETUP.md` for full setup steps and `READY_CHECKLIST.md` for remaining blockers.

---

## First-time run order

```bash
pip install -r requirements.txt
python3 setup.py                    # create .env from template
python3 upload_youtube.py --test-auth
python3 canva_thumbnail.py --auth
python3 canva_thumbnail.py --list-folder
python3 backfill.py --dry-run
python3 backfill.py
python3 pipeline.py --webhook
```
