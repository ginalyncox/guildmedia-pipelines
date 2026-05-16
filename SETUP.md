# Zoom → ffmpeg → YouTube → WordPress Replay Pipeline: Setup Checklist

This document walks through every step required to get the automation pipeline
running — from OAuth credentials to production deployment on Cloudways.

---

## Prerequisites

### System requirements

| Requirement | Minimum version | Check |
|---|---|---|
| Python | 3.10+ | `python3 --version` |
| ffmpeg | 4.x+ | `ffmpeg -version` |
| pip | current | `pip --version` |

### Python packages

```bash
pip install google-api-python-client google-auth-oauthlib flask python-dotenv requests
```

Or pin versions in `requirements.txt`:

```
google-api-python-client>=2.100.0
google-auth-oauthlib>=1.1.0
flask>=3.0.0
python-dotenv>=1.0.0
requests>=2.31.0
```

Then install with:

```bash
pip install -r zoom_pipeline/requirements.txt
```

---

## 1. Zoom Webhook Setup

### 1a. Create a Webhook-Only app (for receiving events)

1. Go to [Zoom App Marketplace](https://marketplace.zoom.us/) → **Develop** → **Build App**.
2. Select **Webhook Only** as the app type.
3. Fill in the app name (e.g., `Ganjier Guild Replay Webhook`) and click **Create**.
4. Under **Feature** → **Event Subscriptions**, enable the **Recording Completed** event:
   - Event type: `recording.completed`
5. Set the **Event notification endpoint URL**:
   - Production: `https://ganjierguild.com/zoom/webhook`
   - Local dev: use an [ngrok](https://ngrok.com/) tunnel, e.g. `https://abc123.ngrok.io/zoom/webhook`
6. Copy the **Secret Token** displayed in the Event Subscriptions section.
7. Add to `.env`:
   ```
   ZOOM_WEBHOOK_SECRET_TOKEN=<paste here>
   ```

### 1b. Create a Server-to-Server OAuth app (for downloading recordings)

1. In Zoom Marketplace → **Develop** → **Build App** → **Server-to-Server OAuth**.
2. Note the **Account ID**, **Client ID**, and **Client Secret** shown on the app credentials page.
3. Add to `.env`:
   ```
   ZOOM_ACCOUNT_ID=<Account ID>
   ZOOM_CLIENT_ID=<Client ID>
   ZOOM_CLIENT_SECRET=<Client Secret>
   ```
4. Grant the app the **Recording: Read** scope.

> **Token expiry note:** Zoom access tokens expire every hour. When
> `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, and `ZOOM_CLIENT_SECRET` are set,
> `pipeline.py` fetches a fresh token automatically before each download.
> This is the **preferred** method. If you set a static `ZOOM_ACCESS_TOKEN`
> instead, you must refresh it manually every hour — not recommended for
> production.

---

## 2. YouTube API / OAuth Setup

### 2a. Create a Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click **Select a project** → **New Project**.
3. Name it `Ganjier Guild Replays` → **Create**.

### 2b. Enable the YouTube Data API v3

1. In the project, go to **APIs & Services** → **Library**.
2. Search for `YouTube Data API v3` → **Enable**.

### 2c. Create OAuth 2.0 credentials

1. Go to **APIs & Services** → **Credentials** → **Create Credentials** → **OAuth client ID**.
2. If prompted, configure the OAuth consent screen first (External, add your Google account as a test user).
3. Application type: **Desktop app**.
4. Name it (e.g., `Replay Pipeline Desktop`).
5. Click **Create** → **Download JSON**.
6. Rename the downloaded file to `client_secrets.json` and place it in the `zoom_pipeline/` directory.

### 2d. Authorize the app

Run the test auth flow to generate `token.json`:

```bash
python zoom_pipeline/upload_youtube.py --test-auth
```

A browser window will open. Sign in with the account that owns the YouTube channel, grant the requested scopes, and the token is saved automatically to `zoom_pipeline/token.json`.

### 2e. Playlist

Set the target playlist name in `.env`:

```
YOUTUBE_PLAYLIST_NAME=Replays
```

The pipeline creates the playlist automatically if it does not already exist on the channel.

> **Security:** Never commit `client_secrets.json` or `token.json` to git.
> Both are listed in `.gitignore` (see Section 9).

---

## 3. WordPress Application Password

### 3a. Generate an Application Password

1. Log in to WordPress Admin at `https://ganjierguild.com/wp-admin`.
2. Go to **Users** → **All Users** → edit your user.
3. Scroll to the **Application Passwords** section.
4. Enter the name `Replay Pipeline` → click **Add New Application Password**.
5. Copy the generated password immediately (it is shown only once).
   Format: `xxxx xxxx xxxx xxxx xxxx xxxx`

### 3b. Add credentials to `.env`

```
WP_USER=your_wp_username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
WP_BASE_URL=https://ganjierguild.com
```

For staging, set `WP_BASE_URL=https://staging.ganjierguild.com`.

### 3c. Confirm the Custom Post Type is REST-enabled

The `gc_replay` CPT must be registered with `show_in_rest => true`. This is
handled by the `guildcommerce-core` plugin. Verify by hitting:

```
GET https://ganjierguild.com/wp-json/wp/v2/gc_replay
```

A valid JSON response (even an empty array) confirms the endpoint is live.

---

## 4. .env File

Create `zoom_pipeline/.env` from this template. **Never commit this file.**

```dotenv
# ── Zoom ────────────────────────────────────────────────────────────────────

# Server-to-Server OAuth (preferred — auto-refreshes tokens)
ZOOM_ACCOUNT_ID=              # From Zoom Marketplace Server-to-Server app
ZOOM_CLIENT_ID=               # From Zoom Marketplace Server-to-Server app
ZOOM_CLIENT_SECRET=           # From Zoom Marketplace Server-to-Server app

# Webhook signature validation
ZOOM_WEBHOOK_SECRET_TOKEN=    # Secret Token from Zoom Webhook-Only app

# Static fallback token (only if NOT using Server-to-Server OAuth above)
# ZOOM_ACCESS_TOKEN=          # Expires every 1 hour — not recommended

# ── YouTube ──────────────────────────────────────────────────────────────────

# Path to OAuth credentials file (relative to zoom_pipeline/)
YOUTUBE_CLIENT_SECRETS_FILE=client_secrets.json

# Path where the OAuth token is saved after first auth
YOUTUBE_TOKEN_FILE=token.json

# Playlist to add uploads to (created automatically if missing)
YOUTUBE_PLAYLIST_NAME=Replays

# ── WordPress ────────────────────────────────────────────────────────────────

WP_BASE_URL=https://ganjierguild.com   # No trailing slash
WP_USER=                               # WordPress username
WP_APP_PASSWORD=                       # Application Password (spaces OK)

# ── ffmpeg ───────────────────────────────────────────────────────────────────

# Seconds to trim from the start of every recording (removes pre-roll)
TRIM_START_SECONDS=30

# Seconds to trim from the end of every recording (removes post-roll)
TRIM_END_SECONDS=10

# ── Canva Thumbnails (optional — omit to use YouTube auto-thumbnails) ────────

CANVA_API_KEY=your_canva_api_key
CANVA_THUMBNAIL_FOLDER_ID=your_folder_id

# ── Backfill (optional — only needed when running backfill.py) ────────────────

# Earliest recording date to include (ISO date, inclusive)
BACKFILL_FROM_DATE=2020-01-01

# Only backfill recordings whose topic contains this string (case-insensitive)
BACKFILL_TOPIC_FILTER=Guild

# Seconds to pause between recordings to avoid Zoom/YouTube rate limits
BACKFILL_DELAY_SECONDS=5

# ── Pipeline ─────────────────────────────────────────────────────────────────

# Local directory where downloaded Zoom recordings are saved temporarily
DOWNLOAD_DIR=./downloads

# Local directory for trimmed output files before YouTube upload
OUTPUT_DIR=./trimmed

# Flask webhook listener port
WEBHOOK_PORT=5055

# Log level: DEBUG | INFO | WARNING | ERROR
LOG_LEVEL=INFO
```

---

## 5. Canva Thumbnail Setup

Canva thumbnails are **optional**. If `CANVA_API_KEY` is not set, the pipeline
silently falls back to YouTube auto-generated thumbnails.

### 5a. Create a Canva API integration

1. Go to [canva.com/developers](https://www.canva.com/developers/) → **Create an integration** → pick **Connect API**.
2. Grant the following scopes:
   - `folder:read`
   - `design:content:read`
   - `design:meta:read`
3. Copy the generated API key.
4. Add to `.env`:
   ```
   CANVA_API_KEY=your_canva_api_key
   ```

### 5b. Find your thumbnail folder ID

```bash
python canva_thumbnail.py --list-folders
```

This prints all folders visible to your integration. Copy the folder ID that
contains your meeting thumbnail designs and add it to `.env`:

```
CANVA_THUMBNAIL_FOLDER_ID=your_folder_id
```

### 5c. Verify designs are visible

```bash
python canva_thumbnail.py --list-folder
```

Confirm that the designs you expect to use as thumbnails appear in the output.

### 5d. Test title matching

```bash
python canva_thumbnail.py --match "Your Meeting Title"
```

This performs the same fuzzy-match logic that `pipeline.py` uses at runtime.
A matching design name and a local PNG path will be printed on success.

> **Note:** If `CANVA_API_KEY` is not set in `.env`, the pipeline silently
> falls back to YouTube auto-thumbnails — no errors are raised and no action
> is required.

---

## 6. First Run Test

Work through each component in isolation before running the full pipeline.

### Step 1 — Test ffmpeg trim

```bash
ffmpeg -i sample_recording.mp4 \
  -ss 30 -to $(ffprobe -v quiet -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 sample_recording.mp4 \
    | awk '{print $1 - 10}') \
  -c copy trimmed_output.mp4
```

Confirm `trimmed_output.mp4` plays correctly, with the expected pre-roll and
post-roll removed.

### Step 2 — Test YouTube upload

```bash
python zoom_pipeline/upload_youtube.py \
  --file trimmed_output.mp4 \
  --title "Test Replay Upload" \
  --description "Pipeline smoke test"
```

Check your YouTube Studio dashboard for the uploaded video and confirm it
appears in the **Replays** playlist.

### Step 3 — Test WordPress post creation

```bash
python zoom_pipeline/post_wordpress.py \
  --title "Test Replay" \
  --youtube-url "https://youtu.be/VIDEO_ID" \
  --description "Smoke test post — delete after confirming."
```

Verify a `gc_replay` post is created in WordPress admin under **Replays**.

### Step 4 — Full pipeline with a sample payload

Create a minimal test payload file `zoom_pipeline/test_payload.json`:

```json
{
  "event": "recording.completed",
  "payload": {
    "object": {
      "id": "TEST_MEETING_ID",
      "topic": "Ganjier Guild Open Session — Test",
      "start_time": "2026-05-10T20:00:00Z",
      "duration": 60,
      "recording_files": [
        {
          "id": "TEST_FILE_ID",
          "file_type": "MP4",
          "download_url": "https://zoom.us/rec/download/TEST",
          "file_size": 104857600,
          "status": "completed"
        }
      ]
    }
  }
}
```

Send it to the local webhook listener:

```bash
# Terminal 1 — start the listener
python zoom_pipeline/pipeline.py

# Terminal 2 — POST the sample payload
curl -X POST http://localhost:5055/zoom/webhook \
  -H "Content-Type: application/json" \
  -d @zoom_pipeline/test_payload.json
```

Watch the logs for each pipeline stage: download → trim → YouTube upload →
WordPress post creation.

---

## 7. Production Deployment (Cloudways)

### 7a. Upload project files

```bash
# From your local machine (repo root)
tar -czf /tmp/zoom_pipeline.tar.gz zoom_pipeline/
scp -P 2412 -i ~/.ssh/cloudways_ganjier \
  /tmp/zoom_pipeline.tar.gz \
  admin@ssh.stagingapp23110.cloudwayssites.com:/tmp/

# On the server
ssh -p 2412 -i ~/.ssh/cloudways_ganjier \
  admin@ssh.stagingapp23110.cloudwayssites.com
cd /var/www/html/public_html
tar -xzf /tmp/zoom_pipeline.tar.gz
```

### 7b. Install dependencies on server

```bash
cd /var/www/html/public_html/zoom_pipeline
pip3 install -r requirements.txt
```

### 7c. Run as a systemd service

Create `/etc/systemd/system/zoom-pipeline.service`:

```ini
[Unit]
Description=Zoom Replay Pipeline Webhook Listener
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/var/www/html/public_html/zoom_pipeline
EnvironmentFile=/var/www/html/public_html/zoom_pipeline/.env
ExecStart=/usr/bin/python3 pipeline.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable zoom-pipeline
sudo systemctl start zoom-pipeline
sudo systemctl status zoom-pipeline
```

Alternatively, use **Supervisor** if systemd is unavailable:

```ini
[program:zoom-pipeline]
command=python3 /var/www/html/public_html/zoom_pipeline/pipeline.py
directory=/var/www/html/public_html/zoom_pipeline
autostart=true
autorestart=true
stderr_logfile=/var/log/zoom-pipeline.err.log
stdout_logfile=/var/log/zoom-pipeline.out.log
```

### 7d. nginx reverse proxy

Add a `location` block inside the `ganjierguild.com` server block:

```nginx
location /zoom/webhook {
    proxy_pass         http://127.0.0.1:5055/zoom/webhook;
    proxy_set_header   Host $host;
    proxy_set_header   X-Real-IP $remote_addr;
    proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 30s;
}
```

Reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

SSL is already managed by Cloudways — no additional certificate configuration
is needed.

### 7e. Cron fallback (optional)

If the Zoom webhook proves unreliable, add a cron job to poll the Zoom API
for new recordings every 30 minutes:

```bash
# crontab -e
*/30 * * * * /usr/bin/python3 /var/www/html/public_html/zoom_pipeline/poll_zoom.py >> /var/log/zoom-poll.log 2>&1
```

`poll_zoom.py` queries the Zoom List Recordings endpoint and processes any
recording not already present in a local state file (`processed_ids.json`).

---

## 8. Backfill (Catch Up Existing Recordings)

Use `backfill.py` to process Zoom recordings that were made before the webhook
was set up, or any recordings the pipeline missed.

### Prerequisites

Ensure these vars are set in `.env` (same credentials used for the webhook setup):

```
ZOOM_ACCOUNT_ID=
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
```

### Optional backfill controls

```dotenv
# How far back to look for recordings (default: all available)
BACKFILL_FROM_DATE=2020-01-01

# Only process meetings whose topic contains this string (case-insensitive)
BACKFILL_TOPIC_FILTER=Guild

# Seconds to wait between recordings to avoid rate limits (default: 5)
BACKFILL_DELAY_SECONDS=5
```

### Running the backfill

1. **Dry run first** — lists what would be processed without doing anything:
   ```bash
   python backfill.py --dry-run
   ```

2. **Review** the printed list, then run for real:
   ```bash
   python backfill.py
   ```

3. **State is saved** to `logs/backfill_state.json` after each recording.
   It is safe to interrupt (`Ctrl+C`) and resume — already-processed recordings
   are skipped automatically.

4. **Retry failures** from a previous run:
   ```bash
   python backfill.py --retry-failed
   ```

---

## 9. Security Notes

| Rule | Details |
|---|---|
| Validate webhook signatures | Every incoming request to `/zoom/webhook` must pass HMAC-SHA256 signature validation using `ZOOM_WEBHOOK_SECRET_TOKEN`. This is implemented in `pipeline.py`. Reject any request that fails validation with HTTP 401. |
| Never commit secrets | `.env`, `token.json`, and `client_secrets.json` must all be in `.gitignore`. |
| Rotate credentials if exposed | If any secret is accidentally committed, rotate it immediately in the respective dashboard (Zoom Marketplace, Google Cloud Console, WordPress Application Passwords). |
| HTTPS only | The webhook endpoint must be served over HTTPS. Zoom will not deliver events to plain HTTP endpoints in production. |
| Restrict file permissions | `chmod 600 zoom_pipeline/.env zoom_pipeline/token.json zoom_pipeline/client_secrets.json` on the server. |

### Required `.gitignore` entries

```gitignore
zoom_pipeline/.env
zoom_pipeline/token.json
zoom_pipeline/client_secrets.json
zoom_pipeline/downloads/
zoom_pipeline/trimmed/
zoom_pipeline/processed_ids.json
```
