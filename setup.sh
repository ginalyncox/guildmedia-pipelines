#!/bin/bash
set -e

echo "============================================"
echo " Ganjier Guild Replay Pipeline Setup"
echo "============================================"
echo

# Step 1 — Install dependencies
echo "[1/5] Installing Python dependencies..."
pip install google-api-python-client google-auth-oauthlib flask python-dotenv requests
echo "Done."
echo

# Step 2 — Create .env
echo "[2/5] Creating .env file..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo ".env created from template."
    echo
    echo "*** IMPORTANT: Open .env and fill in:"
    echo "    ZOOM_WEBHOOK_SECRET_TOKEN"
    echo "    WP_APP_PASSWORD"
    echo
    read -p "Press Enter when .env is filled in..."
else
    echo ".env already exists, skipping."
fi
echo

# Step 3 — Check client_secrets.json
echo "[3/5] Checking for client_secrets.json..."
if [ ! -f client_secrets.json ]; then
    echo "ERROR: client_secrets.json not found."
    echo "Copy it from Downloads and rename to client_secrets.json"
    read -p "Press Enter once it's in this folder..."
else
    echo "client_secrets.json found."
fi
echo

# Step 4 — YouTube auth
echo "[4/5] YouTube authentication..."
echo "A browser window will open — log in as navigators@ganjierguild.com and click Allow."
echo
python upload_youtube.py --test-auth
echo "YouTube auth complete. token.json saved."
echo

# Step 5 — Canva auth
echo "[5/5] Canva authentication..."
echo "A browser window will open — log into Canva and click Allow."
echo
python canva_thumbnail.py --auth
echo "Canva auth complete. canva_token.json saved."
echo

echo "Listing your Canva folders..."
python canva_thumbnail.py --list-folders
echo
read -p "Paste CANVA_THUMBNAIL_FOLDER_ID into .env, then press Enter..."
echo

# Dry run
echo "============================================"
echo " Running backfill dry-run preview..."
echo "============================================"
echo
python backfill.py --dry-run
echo
echo "============================================"
echo " Setup complete!"
echo " To run the real backfill:  python backfill.py"
echo " To start live listener:    python pipeline.py --webhook"
echo "============================================"
