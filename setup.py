"""
setup.py -- Ganjier Guild Replay Pipeline
Creates a .env template from .env.example.
Run with:  python setup.py
"""
import os
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SCRIPT_DIR, ".env")
EXAMPLE_PATH = os.path.join(SCRIPT_DIR, ".env.example")


def main():
    if os.path.exists(ENV_PATH):
        print(f".env already exists at: {ENV_PATH}")
        print("Delete it first if you want to regenerate from .env.example.")
        return

    if not os.path.exists(EXAMPLE_PATH):
        print(f"ERROR: {EXAMPLE_PATH} not found.")
        sys.exit(1)

    shutil.copy(EXAMPLE_PATH, ENV_PATH)
    print(f"Created .env from .env.example at: {ENV_PATH}")
    print("Fill in your credentials, then run:")
    print("  pip install -r requirements.txt")
    print("  python troubleshoot.py")
    print("  python upload_youtube.py --test-auth")
    print("  python canva_thumbnail.py --auth")
    print("  python backfill.py --dry-run")
    print("  python pipeline.py --webhook")


if __name__ == "__main__":
    main()
