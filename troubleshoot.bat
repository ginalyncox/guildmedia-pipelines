@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

echo ============================================================
echo  Ganjier Guild Replay Pipeline -- Diagnostic Tool
echo ============================================================
echo.

:: ---------------------------------------------------------------------------
:: 1. Python
:: ---------------------------------------------------------------------------
echo [1/6] Python...
set PYTHON=
for %%P in (py python python3) do (
    if not defined PYTHON (
        %%P --version >nul 2>&1 && set PYTHON=%%P
    )
)
if not defined PYTHON (
    echo   FAIL  Python not found. Install from https://python.org
    goto :summary
) else (
    for /f "tokens=*" %%V in ('!PYTHON! --version 2^>^&1') do echo   OK    %%V  ^(command: !PYTHON!^)
)

:: ---------------------------------------------------------------------------
:: 2. .env file
:: ---------------------------------------------------------------------------
echo.
echo [2/6] .env file...
if not exist ".env" (
    echo   FAIL  .env not found. Run setup.bat first.
    goto :summary
) else (
    echo   OK    .env found
)

:: Check each required key
set MISSING=0
for %%K in (
    ZOOM_JWARD_ACCOUNT_ID
    ZOOM_JWARD_CLIENT_ID
    ZOOM_JWARD_CLIENT_SECRET
    ZOOM_NAVIGATORS_ACCOUNT_ID
    ZOOM_NAVIGATORS_CLIENT_ID
    ZOOM_NAVIGATORS_CLIENT_SECRET
    ZOOM_JWARD_WEBHOOK_SECRET
    ZOOM_NAVIGATORS_WEBHOOK_SECRET
    YOUTUBE_PLAYLIST_NAME
    WP_BASE_URL
    WP_USER
    WP_APP_PASSWORD
    CANVA_CLIENT_ID
    CANVA_CLIENT_SECRET
    CANVA_THUMBNAIL_FOLDER_ID
) do (
    findstr /i "^%%K=" .env >nul 2>&1
    if errorlevel 1 (
        echo   MISSING  %%K
        set MISSING=1
    ) else (
        :: Check it's not blank
        for /f "tokens=1,* delims==" %%A in ('findstr /i "^%%K=" .env') do (
            if "%%B"=="" (
                echo   BLANK    %%K  ^(key exists but has no value^)
                set MISSING=1
            ) else (
                echo   OK    %%K
            )
        )
    )
)

:: ---------------------------------------------------------------------------
:: 3. Python packages
:: ---------------------------------------------------------------------------
echo.
echo [3/6] Python packages...
for %%P in (requests flask python-dotenv google-auth google-auth-oauthlib google-api-python-client) do (
    !PYTHON! -c "import importlib; importlib.import_module('%%P'.replace('-','_').split('.')[0])" >nul 2>&1
    if errorlevel 1 (
        echo   MISSING  %%P  -- run: pip install %%P
    ) else (
        echo   OK    %%P
    )
)
:: ffmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo   MISSING  ffmpeg -- download from https://ffmpeg.org/download.html
) else (
    echo   OK    ffmpeg
)

:: ---------------------------------------------------------------------------
:: 4. Auth token files
:: ---------------------------------------------------------------------------
echo.
echo [4/6] Auth token files...
if exist "token.json" (
    echo   OK    token.json  ^(YouTube OAuth^)
) else (
    echo   MISSING  token.json -- run: py upload_youtube.py --test-auth
)
if exist "canva_token.json" (
    echo   OK    canva_token.json  ^(Canva OAuth^)
) else (
    echo   MISSING  canva_token.json -- run: py canva_thumbnail.py --auth
)
if exist "client_secrets.json" (
    echo   OK    client_secrets.json  ^(Google OAuth^)
) else (
    echo   MISSING  client_secrets.json -- download from Google Cloud Console
)

:: ---------------------------------------------------------------------------
:: 5. Service connectivity tests
:: ---------------------------------------------------------------------------
echo.
echo [5/6] Service connectivity...
!PYTHON! -c "import requests; r=requests.get('https://api.zoom.us/v2/users/me',timeout=5); print('  OK    Zoom API reachable (HTTP',r.status_code,')')" 2>nul || echo   WARN  Could not reach Zoom API
!PYTHON! -c "import requests; r=requests.get('https://api.canva.com/rest/v1',timeout=5); print('  OK    Canva API reachable (HTTP',r.status_code,')')" 2>nul || echo   WARN  Could not reach Canva API
!PYTHON! -c "import requests; r=requests.get('https://ganjierguild.com/wp-json/wp/v2',timeout=5); print('  OK    WordPress REST API reachable (HTTP',r.status_code,')')" 2>nul || echo   WARN  Could not reach WordPress REST API

:: ---------------------------------------------------------------------------
:: 6. Quick script syntax check
:: ---------------------------------------------------------------------------
echo.
echo [6/6] Script syntax check...
for %%F in (pipeline.py backfill.py canva_thumbnail.py upload_youtube.py trim_video.py post_to_replay_library.py) do (
    !PYTHON! -m py_compile %%F >nul 2>&1
    if errorlevel 1 (
        echo   FAIL  %%F has a syntax error -- run: py %%F
    ) else (
        echo   OK    %%F
    )
)

:: ---------------------------------------------------------------------------
:: Summary
:: ---------------------------------------------------------------------------
:summary
echo.
echo ============================================================
echo  Diagnostic complete.
echo  Fix any FAIL / MISSING / BLANK items above, then run:
echo    py canva_thumbnail.py --auth
echo    py backfill.py --dry-run
echo    py pipeline.py --webhook
echo ============================================================
echo.
pause
