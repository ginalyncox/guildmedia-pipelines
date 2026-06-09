"""
troubleshoot.py -- Ganjier Guild Replay Pipeline Diagnostic
Run with:  py troubleshoot.py
"""
import importlib
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

REQUIRED_KEYS = [
    "ZOOM_JWARD_ACCOUNT_ID",
    "ZOOM_JWARD_CLIENT_ID",
    "ZOOM_JWARD_CLIENT_SECRET",
    "ZOOM_NAVIGATORS_ACCOUNT_ID",
    "ZOOM_NAVIGATORS_CLIENT_ID",
    "ZOOM_NAVIGATORS_CLIENT_SECRET",
    "ZOOM_JWARD_WEBHOOK_SECRET",
    "ZOOM_NAVIGATORS_WEBHOOK_SECRET",
    "YOUTUBE_PLAYLIST_NAME",
    "WP_BASE_URL",
    "WP_USER",
    "WP_APP_PASSWORD",
    "CANVA_CLIENT_ID",
    "CANVA_CLIENT_SECRET",
    "CANVA_THUMBNAIL_FOLDER_ID",
]

REQUIRED_PACKAGES = [
    "requests",
    "flask",
    "dotenv",        # python-dotenv
    "google.auth",   # google-auth
    "googleapiclient",  # google-api-python-client
]

SCRIPTS = [
    "pipeline.py",
    "backfill.py",
    "poll_zoom.py",
    "zoom_auth.py",
    "zoom_verify.py",
    "canva_thumbnail.py",
    "upload_youtube.py",
    "trim_video.py",
    "post_to_replay_library.py",
    "replay_tracker.py",
    "replay_intro.py",
    "mec_events.py",
]

SERVICES = [
    ("Zoom API",          "https://api.zoom.us/v2/users/me"),
    ("Canva API",         "https://api.canva.com/rest/v1"),
    ("WordPress REST API","https://ganjierguild.com/wp-json/wp/v2"),
]

ok   = lambda msg: print(f"  OK       {msg}")
fail = lambda msg: print(f"  FAIL     {msg}")
warn = lambda msg: print(f"  WARN     {msg}")
miss = lambda msg: print(f"  MISSING  {msg}")

errors = 0

def check(label, passed, fix=""):
    global errors
    if passed:
        ok(label)
    else:
        fail(f"{label}  →  {fix}" if fix else label)
        errors += 1

print("=" * 60)
print("  Ganjier Guild Replay Pipeline -- Diagnostic")
print("=" * 60)

# --- 1. Python version ---
print(f"\n[1/7] Python...")
ok(f"Python {sys.version.split()[0]}  (executable: {sys.executable})")

# --- 2. .env file ---
print(f"\n[2/7] .env file...")
env_path = os.path.join(SCRIPT_DIR, ".env")
if not os.path.exists(env_path):
    fail(f".env not found at {env_path}  →  run: python setup.py")
    errors += 1
else:
    ok(f".env found at {env_path}")
    # Parse values
    env_vals = {}
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_vals[k.strip()] = v.strip()

    for key in REQUIRED_KEYS:
        if key not in env_vals:
            miss(f"{key}  →  add to .env")
            errors += 1
        elif not env_vals[key]:
            fail(f"{key} is blank  →  fill in .env")
            errors += 1
        else:
            ok(key)

# --- 3. Python packages ---
print(f"\n[3/7] Python packages...")
for pkg in REQUIRED_PACKAGES:
    mod = pkg.split(".")[0]
    try:
        importlib.import_module(mod)
        ok(pkg)
    except ImportError:
        miss(f"{pkg}  →  pip install {pkg.replace('.','_')}")
        errors += 1

# ffmpeg
try:
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
    ok("ffmpeg")
except Exception:
    miss("ffmpeg  →  https://ffmpeg.org/download.html")
    errors += 1

# --- 4. Auth token files ---
print(f"\n[4/7] Auth token files...")
try:
    from oauth_files import canva_token_ready, ensure_canva_token_file, ensure_youtube_oauth_files, youtube_files_ready
    ensure_youtube_oauth_files()
    ensure_canva_token_file()
except ImportError:
    youtube_files_ready = lambda: False  # type: ignore
    canva_token_ready = lambda: False  # type: ignore

files = {
    "token.json": (
        youtube_files_ready(),
        "python upload_youtube.py --test-auth  (or set YOUTUBE_TOKEN_JSON in .env)",
    ),
    "canva_token.json": (
        canva_token_ready(),
        "python canva_thumbnail.py --auth  (or set CANVA_TOKEN_JSON in .env)",
    ),
    "client_secrets.json": (
        os.path.exists(os.path.join(SCRIPT_DIR, "client_secrets.json")),
        "download from Google Cloud Console  (or set GOOGLE_CLIENT_SECRETS_JSON in .env)",
    ),
}
for fname, (present, fix) in files.items():
    check(fname, present, fix)

# --- 4b. Replay tracker (WordPress plugin / optional Sheets) ---
print(f"\n[4b/7] Replay tracker...")
try:
    from oauth_files import ensure_service_account_file, service_account_ready
    from replay_tracker import (
        sheets_is_configured,
        tracker_backend,
        wp_is_configured,
    )

    ensure_service_account_file()
    backend = tracker_backend()
    ok(f"Tracker backend mode: {backend}")
    if backend in {"wordpress", "both"}:
        if wp_is_configured():
            ok("WordPress tracker credentials present (install ganjier-replay-pipeline plugin)")
        else:
            warn("WordPress tracker credentials missing")
    if backend in {"sheets", "both"}:
        if sheets_is_configured():
            ok("Google Sheets tracker configured")
        else:
            warn(
                "Sheets mirror not configured — set GOOGLE_SHEETS_SPREADSHEET_ID and "
                "GOOGLE_SERVICE_ACCOUNT_JSON if needed"
            )
except ImportError as exc:
    warn(f"replay_tracker import failed ({exc})")

# --- 5. Zoom OAuth per account ---
print(f"\n[5/7] Zoom OAuth...")
try:
    from zoom_auth import ACCOUNT_ENV_PREFIXES, _build_account_auth, auth_status
    for name in ACCOUNT_ENV_PREFIXES:
        auth = _build_account_auth(name, ACCOUNT_ENV_PREFIXES[name])
        if auth is None:
            miss(f"Zoom [{name}]  →  set {ACCOUNT_ENV_PREFIXES[name]}_* in .env")
            errors += 1
            continue
        ok_auth, message = auth_status(auth)
        if ok_auth:
            ok(f"Zoom OAuth [{name}]")
        else:
            fail(f"Zoom OAuth [{name}]  →  {message}")
            if "invalid client" in message.lower() or "invalid_client" in message.lower():
                fail(
                    f"Zoom [{name}] credentials rejected by Zoom — copy fresh values "
                    f"from the {name} Server-to-Server OAuth app in the Zoom Marketplace "
                    f"into {ACCOUNT_ENV_PREFIXES[name]}_* in your GitHub .env"
                )
            errors += 1
except ImportError as exc:
    warn(f"zoom_auth import failed ({exc})")

# --- 6. Service connectivity ---
print(f"\n[6/7] Service connectivity...")
try:
    import requests
    for name, url in SERVICES:
        try:
            r = requests.get(url, timeout=5)
            ok(f"{name}  (HTTP {r.status_code})")
        except Exception as e:
            warn(f"{name} unreachable  ({e})")
except ImportError:
    warn("requests not installed — skipping connectivity checks")

# --- 7. Script syntax ---
print(f"\n[7/7] Script syntax check...")
for script in SCRIPTS:
    path = os.path.join(SCRIPT_DIR, script)
    if not os.path.exists(path):
        miss(f"{script} not found")
        errors += 1
        continue
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", path],
        capture_output=True
    )
    check(script, result.returncode == 0, result.stderr.decode().strip())

# --- Summary ---
print()
print("=" * 60)
if errors == 0:
    print("  All checks passed. Ready to run:")
    print("    python canva_thumbnail.py --auth")
    print("    python backfill.py --dry-run")
    print("    python pipeline.py --webhook")
else:
    print(f"  {errors} issue(s) found. Fix items marked FAIL/MISSING above.")
print("=" * 60)
if sys.stdin.isatty():
    input("\nPress Enter to exit...")
