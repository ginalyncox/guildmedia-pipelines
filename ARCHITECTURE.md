# Zoom Replay Pipeline — Architecture Reference

---

## Data Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          ZOOM CLOUD                                      │
│  Recording ends → Zoom processes MP4 → fires recording.completed event  │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │  HTTPS POST  (webhook payload + HMAC sig)
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    pipeline.py  (Flask, port 5055)                       │
│                                                                          │
│  1. Validate HMAC-SHA256 signature  (ZOOM_WEBHOOK_SECRET_TOKEN)          │
│  2. Parse recording.completed payload                                    │
│  3. Fetch fresh Zoom access token  (Server-to-Server OAuth)              │
│  4. Download MP4 from Zoom Cloud Storage                                 │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │  local MP4 file
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    ffmpeg  (subprocess call)                             │
│                                                                          │
│  -ss TRIM_START_SECONDS  -to (duration - TRIM_END_SECONDS)  -c copy     │
│  Input:  downloads/<meeting_id>.mp4                                      │
│  Output: trimmed/<meeting_id>_trimmed.mp4                                │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │  trimmed MP4 file
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    upload_youtube.py                                     │
│                                                                          │
│  • OAuth 2.0 via token.json  (auto-refreshed)                           │
│  • Uploads video via YouTube Data API v3  (resumable upload)            │
│  • Adds video to YOUTUBE_PLAYLIST_NAME playlist                         │
│  • Returns: YouTube video ID + public URL                               │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │  YouTube video ID + URL
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    post_wordpress.py                                     │
│                                                                          │
│  • Authenticates via Application Password  (WP_USER / WP_APP_PASSWORD)  │
│  • POST /wp-json/wp/v2/gc_replay                                         │
│  • Sets title, content (embedded YouTube player), meta fields           │
│  • Status: publish  (or draft if DRAFT_MODE=true)                       │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │  gc_replay post created
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│              ganjierguild.com — Replay Library Page                      │
│                                                                          │
│  Archive of gc_replay CPT posts, each with embedded YouTube player,     │
│  meeting title, date, and description — served by guildcommerce-theme.  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Component Table

| Component | Role | Technology |
|---|---|---|
| **Zoom Cloud** | Records meetings, stores MP4s, fires webhook events | Zoom API v2 |
| **pipeline.py** | Webhook listener; orchestrates the full pipeline | Python 3.10+, Flask |
| **Zoom Server-to-Server OAuth** | Provides expiring access tokens for recording downloads | Zoom Marketplace OAuth app |
| **ffmpeg** | Trims pre-roll and post-roll from raw MP4 | ffmpeg (subprocess) |
| **upload_youtube.py** | Uploads trimmed video to YouTube, adds to playlist | YouTube Data API v3, google-api-python-client |
| **post_wordpress.py** | Creates a `gc_replay` CPT post with embedded video | WordPress REST API, requests |
| **guildcommerce-core plugin** | Registers `gc_replay` CPT with `show_in_rest=true` | PHP, WordPress |
| **guildcommerce-theme** | Renders the Replay Library archive and single-replay template | PHP, WordPress |
| **nginx** | Reverse-proxies `https://ganjierguild.com/zoom/webhook` → `localhost:5055` | nginx |
| **Cloudways** | Hosts the pipeline process, manages SSL, server environment | Ubuntu, systemd/supervisor |
| **poll_zoom.py** *(fallback)* | Polls Zoom List Recordings API every 30 min when webhook is unreliable | Python, cron |

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `ZOOM_ACCOUNT_ID` | Yes* | Zoom Server-to-Server OAuth Account ID |
| `ZOOM_CLIENT_ID` | Yes* | Zoom Server-to-Server OAuth Client ID |
| `ZOOM_CLIENT_SECRET` | Yes* | Zoom Server-to-Server OAuth Client Secret |
| `ZOOM_WEBHOOK_SECRET_TOKEN` | Yes | HMAC-SHA256 key for validating incoming webhook payloads |
| `ZOOM_ACCESS_TOKEN` | No† | Static Zoom access token (fallback only; expires hourly) |
| `YOUTUBE_CLIENT_SECRETS_FILE` | Yes | Path to `client_secrets.json` (relative to `zoom_pipeline/`) |
| `YOUTUBE_TOKEN_FILE` | Yes | Path to `token.json` (written after first auth) |
| `YOUTUBE_PLAYLIST_NAME` | Yes | Name of playlist to add uploads to; created if missing |
| `WP_BASE_URL` | Yes | WordPress site URL, no trailing slash |
| `WP_USER` | Yes | WordPress username with Application Password |
| `WP_APP_PASSWORD` | Yes | WordPress Application Password |
| `TRIM_START_SECONDS` | Yes | Seconds to cut from recording start (removes pre-roll) |
| `TRIM_END_SECONDS` | Yes | Seconds to cut from recording end (removes post-roll) |
| `DOWNLOAD_DIR` | Yes | Local temp directory for raw Zoom MP4 downloads |
| `OUTPUT_DIR` | Yes | Local directory for trimmed MP4s before upload |
| `WEBHOOK_PORT` | Yes | Port Flask listens on (default: `5055`) |
| `LOG_LEVEL` | No | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

\* `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, and `ZOOM_CLIENT_SECRET` are required
unless `ZOOM_ACCESS_TOKEN` is set as a static fallback.  
† `ZOOM_ACCESS_TOKEN` is **not recommended** for production because it expires
every hour.

---

## File Structure

```
zoom_pipeline/
│
├── pipeline.py               # Flask app — webhook endpoint + pipeline orchestrator
├── upload_youtube.py         # YouTube upload logic (resumable upload, playlist mgmt)
├── post_wordpress.py         # WordPress REST API client for gc_replay CPT
├── poll_zoom.py              # Optional cron-based fallback poller
│
├── requirements.txt          # Pinned Python dependencies
├── .env                      # Secrets and config — NOT committed to git
├── client_secrets.json       # Google OAuth credentials — NOT committed to git
├── token.json                # Google OAuth token (auto-written) — NOT committed to git
├── processed_ids.json        # State file: Zoom recording IDs already processed
│
├── downloads/                # Temp storage for raw MP4s downloaded from Zoom
│   └── <meeting_id>.mp4
│
├── trimmed/                  # Trimmed MP4s awaiting (or completed) YouTube upload
│   └── <meeting_id>_trimmed.mp4
│
├── logs/
│   └── pipeline.log          # Rotating log file
│
├── SETUP.md                  # This project's setup checklist
├── ARCHITECTURE.md           # This file
└── .gitignore                # Excludes .env, token.json, client_secrets.json, etc.
```

---

## Failure Modes and Fallbacks

| Stage | Failure Mode | Detection | Fallback / Recovery |
|---|---|---|---|
| **Zoom Webhook delivery** | Zoom fails to deliver event (network issue, server down) | Missing recording in Replay Library | `poll_zoom.py` cron runs every 30 min and catches skipped recordings via `processed_ids.json` |
| **HMAC signature validation** | Malformed or spoofed request | `pipeline.py` returns HTTP 401 and logs the rejection | No action taken; Zoom retries legitimate events up to 3 times |
| **Zoom token expiry** | Access token expires mid-pipeline | HTTP 401 from Zoom download URL | `pipeline.py` catches 401 on download, re-fetches token via Server-to-Server OAuth, retries download once |
| **Zoom MP4 download failure** | Zoom download URL returns error or times out | Non-200 HTTP response | Log error, add meeting ID to a `failed_downloads.json` queue; operator can re-trigger manually |
| **ffmpeg trim failure** | Corrupt input file or bad duration metadata | Non-zero ffmpeg exit code | Skip trim step, fall back to uploading the untrimmed raw file with a warning in the logs |
| **YouTube upload failure** | API quota exceeded, token revoked, network timeout | `googleapiclient.errors.HttpError` | Retry with exponential backoff (3 attempts); if all fail, save trimmed file to `failed_uploads/` and alert via log |
| **YouTube quota exhaustion** | Daily upload quota (10,000 units) hit | HTTP 403 `quotaExceeded` | Queue upload for next day; `processed_ids.json` ensures the recording is not re-downloaded |
| **WordPress REST API failure** | Application Password invalid, CPT not REST-enabled, 5xx error | Non-2xx HTTP response | Log error with full response body; YouTube video is already uploaded — operator can create WP post manually or re-run `post_wordpress.py` |
| **`gc_replay` CPT not registered** | `/wp-json/wp/v2/gc_replay` returns 404 | HTTP 404 on POST | Verify `guildcommerce-core` plugin is active and `show_in_rest` is `true`; flush rewrite rules: `wp rewrite flush --hard` |
| **Flask process crash** | Unhandled exception in pipeline | Port 5055 stops responding; Zoom webhook returns timeout | systemd/supervisor auto-restarts the process; review `pipeline.log` for the traceback |
| **Disk full on server** | `downloads/` or `trimmed/` directories fill up | ffmpeg or download write fails | Pipeline cleans up files after successful upload; add a cron to purge files older than 7 days as a safety net |
