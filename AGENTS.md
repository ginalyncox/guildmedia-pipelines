# AGENTS.md

## Project overview

Python automation pipeline for Ganjier Guild Zoom replays: webhook → download → ffmpeg trim → YouTube upload → WordPress replay post. All scripts live at the repo root (flat layout; docs sometimes reference a `zoom_pipeline/` subfolder that does not exist).

## Cursor Cloud specific instructions

### Prerequisites (already on the VM image)

- **Python 3.10+** (`python3`)
- **ffmpeg / ffprobe** (system packages)
- **pip** for Python dependencies

### Dependency refresh (automatic on VM startup)

```bash
pip install google-api-python-client google-auth-oauthlib flask python-dotenv requests
```

### First-time / manual setup

1. Create `.env` in the repo root: `python3 setup.py`
2. Fill in Zoom webhook secrets and any placeholder values (see `SETUP.md` and `READY_CHECKLIST.md`).
3. On Linux, set `TEMP_DIR=/tmp/zoom_pipeline` in `.env` (the template from `setup.py` uses a Windows `%TEMP%` path).
4. Place `client_secrets.json` (Google OAuth desktop credentials) in the repo root — required for YouTube upload.
5. Run one-time OAuth flows (need a browser or Desktop pane):
   - `python3 upload_youtube.py --test-auth` → creates `token.json`
   - `python3 canva_thumbnail.py --auth` → creates `canva_token.json` (optional)

### Running the application

| Task | Command |
|---|---|
| Live webhook listener (port **5055**) | `python3 pipeline.py --webhook` |
| Offline pipeline test | `python3 pipeline.py --file payload.json` |
| Historical recording scan (preview) | `python3 backfill.py --dry-run` |
| Historical recording scan (live) | `python3 backfill.py` |
| Video trim only | `python3 trim_video.py input.mp4 output.mp4` |

Start long-running services in **tmux** (e.g. session `pipeline-webhook`). The webhook binds `0.0.0.0:5055`; route `/zoom/webhook` accepts POST.

Quick smoke test for the webhook (Zoom endpoint validation handshake):

```bash
curl -s http://127.0.0.1:5055/zoom/webhook \
  -H "Content-Type: application/json" \
  -d '{"event":"endpoint.url_validation","payload":{"plainToken":"test-token"}}'
```

### Diagnostics

There is no formal linter or test suite. Use:

```bash
python3 troubleshoot.py          # checks .env, packages, ffmpeg, syntax (interactive prompt at end — pipe newline)
python3 -m py_compile *.py       # syntax-only check
```

### External services (not run locally)

- **Zoom** — S2S OAuth + webhooks (credentials in `.env`)
- **YouTube** — Data API v3 (`client_secrets.json` + `token.json`)
- **WordPress** — `ganjierguild.com` REST API (`WP_USER` + `WP_APP_PASSWORD`)
- **Canva** — optional thumbnails (`canva_token.json`)

Verify WordPress auth without creating a post:

```bash
python3 -c "import os, requests; from dotenv import load_dotenv; load_dotenv(); \
r = requests.get(os.environ['WP_BASE_URL']+'/wp-json/wp/v2/users/me', \
auth=(os.environ['WP_USER'], os.environ['WP_APP_PASSWORD'].replace(' ',''))); \
print(r.status_code, r.json().get('name'))"
```

### Known gaps

- No committed `requirements.txt` or `.env.example`; use `setup.py` / `setup.sh` and `SETUP.md`.
- `setup.sh` is interactive (prompts for OAuth) — prefer non-interactive steps above in cloud VMs.
- Full E2E pipeline needs valid Zoom S2S credentials, `client_secrets.json`, and `token.json`. Zoom OAuth may return 400 if credentials are stale.
- `pipeline.py` imports `trim_recording` / `create_replay_post` but modules export `trim_video` / `post_to_replay_library` — orchestrator may fail at those steps until aligned; component scripts run independently.
