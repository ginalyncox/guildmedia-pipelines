"""
post_to_replay_library.py
=========================
Creates a gc_replay Custom Post Type (CPT) entry in WordPress via the REST API.
Uses Application Passwords for authentication (no cookie auth).

Site:   staging.ganjierguild.com  (or ganjierguild.com for production)
Plugin: guildcommerce-core
CPT:    gc_replay

.env.example template
---------------------
# Copy this block to a .env file and fill in your values.
#
# WP_BASE_URL=https://staging.ganjierguild.com
# WP_USER=your_wp_username
# WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx   # WordPress Application Password (with or without spaces)
#
# Optional overrides:
# GC_REPLAY_ENDPOINT=/wp-json/wp/v2/gc_replay      # default shown; change to /wp-json/wp/v2/replays if needed
# GC_TOPIC_TAXONOMY=gc_topic                        # taxonomy slug used for topic terms
"""

import os
import sys
import json
import base64
import mimetypes
import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config constant — change the endpoint here if the CPT REST base differs
# ---------------------------------------------------------------------------
DEFAULT_CPT_ENDPOINT = f"/wp-json/wp/v2/{os.getenv('WP_REPLAY_CPT', 'gc_replay')}"  # controlled by WP_REPLAY_CPT env var
DEFAULT_TOPIC_TAXONOMY = "gc_topic"                  # taxonomy slug for topic terms


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def post_to_replay_library(
    youtube_video_id: str,
    title: str,
    description: str,
    meeting_date: datetime.datetime,
    topics: list[str],
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    cpt_endpoint: Optional[str] = None,
    topic_taxonomy: Optional[str] = None,
) -> dict:
    """
    Create a CPT post in the WordPress replay library.

    CPT slug is controlled by WP_REPLAY_CPT env var (default: gc_replay, production: replay)

    Parameters
    ----------
    youtube_video_id : str
        The YouTube video ID (e.g. "dQw4w9WgXcQ").
    title : str
        Post title.
    description : str
        Plain-text or HTML description shown below the embed.
    meeting_date : datetime.datetime
        Date/time of the original meeting (used as the post date).
    topics : list[str]
        List of topic term *names* (strings). Terms are created if they don't
        exist; existing terms are looked up by name.
    wp_base_url : str
        WordPress site root, no trailing slash (e.g. "https://staging.ganjierguild.com").
    wp_user : str
        WordPress username.
    wp_app_password : str
        WordPress Application Password (spaces in the password are stripped).
    cpt_endpoint : str, optional
        REST endpoint for the CPT. Defaults to DEFAULT_CPT_ENDPOINT.
    topic_taxonomy : str, optional
        Taxonomy slug for topics. Defaults to DEFAULT_TOPIC_TAXONOMY.

    Returns
    -------
    dict
        {"wp_post_id": int, "wp_post_url": str, "youtube_url": str, "title": str}
    """

    base_url = wp_base_url.rstrip("/")
    endpoint = cpt_endpoint or DEFAULT_CPT_ENDPOINT
    taxonomy = topic_taxonomy or DEFAULT_TOPIC_TAXONOMY

    # Build auth header
    token = base64.b64encode(
        f"{wp_user}:{wp_app_password.replace(' ', '')}".encode()
    ).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    youtube_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
    embed_url = f"https://www.youtube.com/embed/{youtube_video_id}"

    # -----------------------------------------------------------------------
    # 1. Build post content (YouTube iframe + description)
    # -----------------------------------------------------------------------
    iframe = (
        f'<iframe width="560" height="315" '
        f'src="{embed_url}" '
        f'title="YouTube video player" '
        f'frameborder="0" '
        f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
        f'allowfullscreen></iframe>'
    )
    content = f"{iframe}\n\n{description}"

    # -----------------------------------------------------------------------
    # 2. Upload YouTube thumbnail as featured image
    # -----------------------------------------------------------------------
    featured_media_id = _upload_youtube_thumbnail(
        base_url, headers, youtube_video_id, title
    )

    # -----------------------------------------------------------------------
    # 3. Resolve topic term IDs
    # -----------------------------------------------------------------------
    term_ids = _resolve_term_ids(base_url, headers, taxonomy, topics)

    # -----------------------------------------------------------------------
    # 4. POST the gc_replay entry
    # -----------------------------------------------------------------------
    post_date_iso = meeting_date.strftime("%Y-%m-%dT%H:%M:%S")

    payload: dict = {
        "title": title,
        "content": content,
        "status": "publish",
        "date": post_date_iso,
    }

    if featured_media_id:
        payload["featured_media"] = featured_media_id

    if term_ids:
        payload[taxonomy] = term_ids

    post_url = f"{base_url}{endpoint}"
    response = requests.post(post_url, headers=headers, json=payload, timeout=30)

    if not response.ok:
        raise RuntimeError(
            f"Failed to create gc_replay post — "
            f"HTTP {response.status_code}: {response.text}"
        )

    post_data = response.json()
    wp_post_id = post_data["id"]
    wp_post_url = post_data.get("link", f"{base_url}/?p={wp_post_id}")

    print(f"[OK] Created gc_replay post: ID={wp_post_id}  URL={wp_post_url}")

    return {
        "wp_post_id": wp_post_id,
        "wp_post_url": wp_post_url,
        "youtube_url": youtube_url,
        "title": title,
    }


# ---------------------------------------------------------------------------
# Helper: Upload YouTube thumbnail to WP media library
# ---------------------------------------------------------------------------

def _upload_youtube_thumbnail(
    base_url: str,
    auth_headers: dict,
    video_id: str,
    post_title: str,
) -> Optional[int]:
    """
    Fetch the maxresdefault thumbnail from YouTube and upload it to the
    WordPress media library. Returns the attachment ID, or None on failure.
    """
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"

    # Fallback to hqdefault if maxresdefault is not available (returns a
    # 120x90 placeholder for missing videos)
    try:
        img_response = requests.get(thumbnail_url, timeout=15)
        if img_response.status_code != 200 or img_response.headers.get(
            "Content-Length", "9999"
        ) == "2455":
            # YouTube serves a 2455-byte placeholder when maxresdefault is missing
            fallback_url = (
                f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
            )
            img_response = requests.get(fallback_url, timeout=15)
        img_response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[WARN] Could not fetch YouTube thumbnail: {exc}", file=sys.stderr)
        return None

    filename = f"replay-thumbnail-{video_id}.jpg"
    upload_headers = {
        "Authorization": auth_headers["Authorization"],
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": "image/jpeg",
    }

    media_endpoint = f"{base_url}/wp-json/wp/v2/media"
    upload_response = requests.post(
        media_endpoint,
        headers=upload_headers,
        data=img_response.content,
        timeout=30,
    )

    if not upload_response.ok:
        print(
            f"[WARN] Media upload failed — HTTP {upload_response.status_code}: "
            f"{upload_response.text}",
            file=sys.stderr,
        )
        return None

    media_id = upload_response.json().get("id")
    print(f"[OK] Uploaded thumbnail as media ID={media_id}")

    # Optionally update the media alt text
    if media_id:
        patch_headers = dict(upload_headers)
        patch_headers["Content-Type"] = "application/json"
        requests.patch(
            f"{media_endpoint}/{media_id}",
            headers=patch_headers,
            json={"alt_text": post_title},
            timeout=15,
        )

    return media_id


# ---------------------------------------------------------------------------
# Helper: Resolve or create taxonomy term IDs
# ---------------------------------------------------------------------------

def _resolve_term_ids(
    base_url: str,
    auth_headers: dict,
    taxonomy: str,
    term_names: list[str],
) -> list[int]:
    """
    For each term name, look it up in the given taxonomy. If not found, create
    it. Returns a list of term IDs.
    """
    if not term_names:
        return []

    terms_endpoint = f"{base_url}/wp-json/wp/v2/{taxonomy}"
    term_ids: list[int] = []

    for name in term_names:
        # Search for existing term
        search_resp = requests.get(
            terms_endpoint,
            headers=auth_headers,
            params={"search": name, "per_page": 5},
            timeout=15,
        )
        if search_resp.ok:
            matches = [
                t for t in search_resp.json()
                if t.get("name", "").lower() == name.lower()
            ]
            if matches:
                term_ids.append(matches[0]["id"])
                print(f"[OK] Found term '{name}' — ID={matches[0]['id']}")
                continue

        # Create the term if not found
        create_resp = requests.post(
            terms_endpoint,
            headers=auth_headers,
            json={"name": name},
            timeout=15,
        )
        if create_resp.ok:
            new_id = create_resp.json().get("id")
            term_ids.append(new_id)
            print(f"[OK] Created term '{name}' — ID={new_id}")
        else:
            print(
                f"[WARN] Could not resolve/create term '{name}': "
                f"HTTP {create_resp.status_code}: {create_resp.text}",
                file=sys.stderr,
            )

    return term_ids


# ---------------------------------------------------------------------------
# __main__ — standalone test / CLI usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Standalone test runner. Reads credentials from a .env file in the same
    directory (or any parent directory on the dotenv search path).

    Required .env keys:
        WP_BASE_URL        e.g. https://staging.ganjierguild.com
        WP_USER            e.g. admin
        WP_APP_PASSWORD    e.g. xxxx xxxx xxxx xxxx xxxx xxxx

    Optional .env keys:
        GC_REPLAY_ENDPOINT  default: /wp-json/wp/v2/gc_replay
        GC_TOPIC_TAXONOMY   default: gc_topic
    """

    # Load .env from the script's own directory first, then CWD
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, ".env")
    load_dotenv(dotenv_path=dotenv_path)

    wp_base_url = os.environ.get("WP_BASE_URL", "https://staging.ganjierguild.com")
    wp_user = os.environ.get("WP_USER", "")
    wp_app_password = os.environ.get("WP_APP_PASSWORD", "")
    cpt_endpoint = os.environ.get("GC_REPLAY_ENDPOINT", DEFAULT_CPT_ENDPOINT)
    topic_taxonomy = os.environ.get("GC_TOPIC_TAXONOMY", DEFAULT_TOPIC_TAXONOMY)

    if not wp_user or not wp_app_password:
        sys.exit(
            "ERROR: WP_USER and WP_APP_PASSWORD must be set in the environment "
            "or in a .env file next to this script."
        )

    # -------------------------------------------------------------------
    # Sample test payload — edit as needed
    # -------------------------------------------------------------------
    result = post_to_replay_library(
        youtube_video_id="dQw4w9WgXcQ",          # Replace with a real video ID
        title="Test Replay: Ganjier Open Session",
        description=(
            "This is an automatically posted test replay.\n\n"
            "Recorded during the weekly Ganjier Guild open session."
        ),
        meeting_date=datetime.datetime(2025, 5, 10, 18, 0, 0),
        topics=["Open Session", "Testing"],
        wp_base_url=wp_base_url,
        wp_user=wp_user,
        wp_app_password=wp_app_password,
        cpt_endpoint=cpt_endpoint,
        topic_taxonomy=topic_taxonomy,
    )

    print("\nResult:")
    print(json.dumps(result, indent=2))
