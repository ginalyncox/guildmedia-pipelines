@echo off
echo ============================================
echo  Ganjier Guild Replay Pipeline Setup
echo ============================================
echo.

:: Detect which Python command works on this machine
set PYTHON=
py --version >nul 2>&1 && set PYTHON=py
if "%PYTHON%"=="" python --version >nul 2>&1 && set PYTHON=python
if "%PYTHON%"=="" python3 --version >nul 2>&1 && set PYTHON=python3
if "%PYTHON%"=="" (
    echo ERROR: Python not found. Install from https://python.org
    echo Check "Add Python to PATH" during install, then re-run this script.
    pause
    exit /b 1
)
echo Using Python command: %PYTHON%
echo.

:: Step 1 — Install Python dependencies
echo [1/5] Installing Python dependencies...

%PYTHON% -m pip install google-api-python-client google-auth-oauthlib flask python-dotenv requests
if %errorlevel% neq 0 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)
echo Done.
echo.

:: Step 2 — Create .env from template
echo [2/5] Creating .env file...

:: Remove existing .env if present (fixes read-only/access denied)
if exist .env attrib -r .env
if exist .env del /f .env

:: Write .env directly
(
echo # --- Zoom: jward account ---
echo ZOOM_JWARD_ACCOUNT_ID=your_jward_account_id
echo ZOOM_JWARD_CLIENT_ID=your_jward_client_id
echo ZOOM_JWARD_CLIENT_SECRET=your_jward_client_secret
echo.
echo # --- Zoom: navigators account ---
echo ZOOM_NAVIGATORS_ACCOUNT_ID=your_navigators_account_id
echo ZOOM_NAVIGATORS_CLIENT_ID=your_navigators_client_id
echo ZOOM_NAVIGATORS_CLIENT_SECRET=your_navigators_client_secret
echo.
echo # --- Zoom: webhook secrets (one per account) ---
echo ZOOM_JWARD_WEBHOOK_SECRET=your_jward_webhook_secret
echo ZOOM_NAVIGATORS_WEBHOOK_SECRET=your_navigators_webhook_secret
echo.
echo # --- YouTube ---
echo YOUTUBE_PLAYLIST_NAME=Replays
echo YOUTUBE_CHANNEL_ID=UCsaeMxhYi2cpqvzfOBWTDaQ
echo.
echo # --- WordPress ---
echo WP_BASE_URL=https://ganjierguild.com
echo WP_USER=gina.cox
echo WP_APP_PASSWORD=Z4Yt UEq8 xDmR VUbk IXrw dWrv
echo WP_REPLAY_CPT=replay
echo.
echo # --- Canva ---
echo CANVA_CLIENT_ID=OC-AZ4jhERgHtNN
echo CANVA_CLIENT_SECRET=cnvcaOfYK743t-WHG1rG0RO9Icv0B4pzBEEVYkMf44Dad85sbfbaafef
echo CANVA_REDIRECT_URI=http://127.0.0.1:8080/canva/callback
echo CANVA_THUMBNAIL_FOLDER_ID=FAHEEnvFnlM
echo.
echo # --- Backfill ---
echo BACKFILL_FROM_DATE=2023-01-01
echo BACKFILL_TOPIC_FILTER=
echo BACKFILL_DELAY_SECONDS=5
echo.
echo # --- Pipeline ---
echo TEMP_DIR=%TEMP%\zoom_pipeline
) > .env

echo .env created with all known credentials.
echo.
echo .env is fully pre-filled. Opening for review...
notepad .env
echo Press any key when .env is saved and closed...
pause
echo.

:: Step 3 — Place client_secrets.json
echo [3/5] Checking for client_secrets.json...
if not exist client_secrets.json (
    :: Try to auto-copy from Downloads
    if exist "%USERPROFILE%\Downloads\client_secret_649621488796-kini1pcjo1vhedtmn8rm9ce8t9ttnmpj.apps.googleusercontent.com.json" (
        copy "%USERPROFILE%\Downloads\client_secret_649621488796-kini1pcjo1vhedtmn8rm9ce8t9ttnmpj.apps.googleusercontent.com.json" client_secrets.json
        echo client_secrets.json copied from Downloads automatically.
    ) else (
        echo client_secrets.json not found. Copy it from your Downloads folder and rename it to client_secrets.json
        echo.
        echo Press any key once client_secrets.json is in this folder...
        pause
    )
) else (
    echo client_secrets.json found.
)
echo.

:: Step 4 — YouTube OAuth (opens browser)
echo [4/5] YouTube authentication...
echo A browser window will open. Log in as navigators@ganjierguild.com and click Allow.
echo.
%PYTHON% upload_youtube.py --test-auth
if %errorlevel% neq 0 (
    echo ERROR: YouTube auth failed. Check client_secrets.json and try again.
    pause
    exit /b 1
)
echo YouTube auth complete. token.json saved.
echo.

:: Step 5 — Canva OAuth (opens browser)
echo [5/5] Canva authentication...
echo A browser window will open. Log into Canva and click Allow.
echo.
%PYTHON% canva_thumbnail.py --auth
if %errorlevel% neq 0 (
    echo ERROR: Canva auth failed. Check CANVA_CLIENT_ID and CANVA_CLIENT_SECRET in .env.
    pause
    exit /b 1
)
echo Canva auth complete. canva_token.json saved.
echo.

:: Get Canva folder ID
echo Listing your Canva folders...
echo Copy the ID for your thumbnail folder and paste it into .env as CANVA_THUMBNAIL_FOLDER_ID
echo.
%PYTHON% canva_thumbnail.py --list-folders
echo.
echo Press any key once CANVA_THUMBNAIL_FOLDER_ID is added to .env...
pause
echo.

:: Final — Dry run
echo ============================================
echo  Running backfill dry-run preview...
echo ============================================
echo.
%PYTHON% backfill.py --dry-run
echo.
echo ============================================
echo  Setup complete!
echo  Review the dry-run output above.
echo  When ready to run the real backfill:
echo    python backfill.py
echo  To start the live webhook listener:
echo    python pipeline.py --webhook
echo ============================================
pause
