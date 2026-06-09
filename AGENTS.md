# AGENTS.md

## Project overview

Python automation pipeline for Ganjier Guild Zoom replays: webhook → download → ffmpeg trim → YouTube upload → Canva thumbnail (optional) → WordPress replay post. All scripts live at the repo root (flat layout; docs sometimes reference a `zoom_pipeline/` subfolder that does not exist).

**Operator instructions:** see [`WORKFLOW.md`](WORKFLOW.md) for the full runbook. MEC standards: [`MEC_EVENT_STANDARD.md`](MEC_EVENT_STANDARD.md). Copy-paste series blocks: [`MEC_SERIES_TEMPLATES.md`](MEC_SERIES_TEMPLATES.md).

## Cursor Cloud specific instructions

### Prerequisites (already on the VM image)

- **Python 3.10+** (`python3`)
- **ffmpeg / ffprobe** (system packages)
- **pip** for Python dependencies

### Dependency refresh (automatic on VM startup)

```bash
pip install -r requirements.txt
```

### First-time / manual setup

1. Create `.env` in the repo root: `python3 setup.py`
2. Fill in credentials (see `SETUP.md`, `READY_CHECKLIST.md`, and `.env.example`).
3. On Linux, set `TEMP_DIR=/tmp/zoom_pipeline` in `.env`.
4. YouTube: place `client_secrets.json` in repo root **or** set `GOOGLE_CLIENT_SECRETS_JSON` / `YOUTUBE_TOKEN_JSON` in `.env`.
5. Run one-time OAuth flows (need a browser or Desktop pane):
   - `python3 upload_youtube.py --test-auth` → creates `token.json`
   - `python3 canva_thumbnail.py --auth` → creates `canva_token.json` (optional)
6. Set Canva folder: `CANVA_THUMBNAIL_FOLDER_NAME=Replay Thumbnail Folder`

### Running the application

| Task | Command |
|---|---|
| Live webhook listener (port **5055**) | `python3 pipeline.py --webhook` |
| Offline pipeline test | `python3 pipeline.py --file payload.json` |
| Historical recording scan (preview) | `python3 backfill.py --dry-run` |
| Yesterday-only test backfill | `python3 backfill.py --yesterday --dry-run` |
| Historical recording scan (live) | `python3 backfill.py` |
| Missed-webhook poller | `python3 poll_zoom.py` |
| Canva folder / match test | `python3 canva_thumbnail.py --list-folder` / `--match "Topic"` |
| Intro preview | `python3 replay_intro.py build --title "Topic" -o /tmp/intro.mp4` |
| Zoom credential check | `python3 zoom_verify.py` |
| Sheets tracker test | `python3 replay_tracker.py --test` |

Start long-running services in **tmux** (e.g. session `pipeline-webhook`). The webhook binds `0.0.0.0:5055`; route `/zoom/webhook` accepts POST.

Quick smoke test for the webhook (Zoom endpoint validation handshake):

```bash
curl -s http://127.0.0.1:5055/zoom/webhook \
  -H "Content-Type: application/json" \
  -d '{"event":"endpoint.url_validation","payload":{"plainToken":"test-token"}}'
```

### Diagnostics

```bash
python3 troubleshoot.py
python3 zoom_verify.py
python3 -m py_compile *.py
python3 -m unittest tests.test_pipeline_wiring
```

### External services (not run locally)

- **Zoom** — S2S OAuth + webhooks (`ZOOM_JWARD_*`, `ZOOM_NAVIGATORS_*` in `.env`)
- **YouTube** — Data API v3 (`client_secrets.json` + `token.json`, or `.env` JSON vars)
- **WordPress** — `ganjierguild.com` REST API (`WP_USER` + `WP_APP_PASSWORD`)
- **Canva** — OAuth thumbnails from `Replay Thumbnail Folder` (`canva_token.json`)
- **WordPress tracker** — install `wordpress-plugin/ganjier-replay-pipeline/` (Tools → Replay Pipeline dashboard)
- **Google Sheets** — optional mirror during migration (`REPLAY_TRACKER_BACKEND=both`)

Verify WordPress auth without creating a post:

```bash
python3 -c "import os, requests; from dotenv import load_dotenv; load_dotenv(); \
r = requests.get(os.environ['WP_BASE_URL']+'/wp-json/wp/v2/users/me', \
auth=(os.environ['WP_USER'], os.environ['WP_APP_PASSWORD'].replace(' ',''))); \
print(r.status_code, r.json().get('name'))"
```
