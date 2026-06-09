"""
ai_intro.py — AI-generated replay intro (OpenAI image + voiceover + ffmpeg).

Produces a short branded MP4 with:
  1. AI background image (OpenAI Images API)
  2. AI voiceover tagline (OpenAI TTS)
  3. ffmpeg Ken Burns motion + title overlay

Requires OPENAI_API_KEY in .env. Falls back to the ffmpeg text slate when unavailable.

Env:
    REPLAY_INTRO_MODE=ai          # ffmpeg | ai | static
    OPENAI_API_KEY=sk-...
    REPLAY_INTRO_TTS_VOICE=onyx
    REPLAY_INTRO_IMAGE_MODEL=gpt-image-1
"""

from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / ".env")

logger = logging.getLogger("ai_intro")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
REPLAY_INTRO_TTS_VOICE = os.getenv("REPLAY_INTRO_TTS_VOICE", "onyx")
REPLAY_INTRO_IMAGE_MODEL = os.getenv("REPLAY_INTRO_IMAGE_MODEL", "gpt-image-1")
REPLAY_INTRO_SCRIPT_MODEL = os.getenv("REPLAY_INTRO_SCRIPT_MODEL", "gpt-4o-mini")
BRAND_LINE1 = os.getenv("REPLAY_INTRO_LINE1", "Ganjier Guild")
BRAND_LINE2 = os.getenv("REPLAY_INTRO_LINE2", "Replay Library")

OPENAI_BASE = "https://api.openai.com/v1"


def ai_intro_available() -> bool:
    return bool(OPENAI_API_KEY)


def _openai_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }


def build_voiceover_script(meeting_title: str, meeting_date: datetime | None = None) -> str:
    """
    Build a short spoken intro line.

    Uses a lightweight LLM when configured; otherwise a deterministic template.
    """
    title = meeting_title.strip() or "Guild Session"
    date_phrase = ""
    if meeting_date:
        date_phrase = f" from {meeting_date.strftime('%B %-d, %Y')}"

    if not ai_intro_available():
        return (
            f"Welcome to the {BRAND_LINE1} {BRAND_LINE2}. "
            f"Today's session: {title}{date_phrase}."
        )

    prompt = (
        f"Write one sentence (max 22 words) for a professional podcast-style video intro. "
        f"Brand: {BRAND_LINE1}. Series: {BRAND_LINE2}. Session: {title}{date_phrase}. "
        f"Tone: warm, credible, cannabis education community. No quotes or emojis."
    )
    try:
        response = requests.post(
            f"{OPENAI_BASE}/chat/completions",
            headers=_openai_headers(),
            json={
                "model": REPLAY_INTRO_SCRIPT_MODEL,
                "messages": [
                    {"role": "system", "content": "You write concise spoken intro lines."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 80,
                "temperature": 0.7,
            },
            timeout=45,
        )
        if response.ok:
            text = response.json()["choices"][0]["message"]["content"].strip()
            text = text.strip('"').strip()
            if text:
                return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI intro script generation failed, using template: %s", exc)

    return (
        f"Welcome to the {BRAND_LINE1} {BRAND_LINE2}. "
        f"Today's session: {title}{date_phrase}."
    )


def _image_prompt(meeting_title: str) -> str:
    title = meeting_title.strip() or "Guild Session"
    return (
        f"Elegant 16:9 title card background for a professional cannabis education webinar. "
        f"Deep forest green palette, subtle botanical textures, soft gold accents, modern and "
        f"premium. No text, no logos, no people. Session theme: {title}."
    )


def generate_background_image(output_path: Path, meeting_title: str) -> Path:
    """Generate a landscape background PNG via OpenAI Images API."""
    if not ai_intro_available():
        raise RuntimeError("OPENAI_API_KEY is required for AI intro image generation.")

    response = requests.post(
        f"{OPENAI_BASE}/images/generations",
        headers=_openai_headers(),
        json={
            "model": REPLAY_INTRO_IMAGE_MODEL,
            "prompt": _image_prompt(meeting_title),
            "size": "1536x1024",
        },
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(
            f"OpenAI image generation failed HTTP {response.status_code}: {response.text[:500]}"
        )

    data = response.json()["data"][0]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if data.get("b64_json"):
        output_path.write_bytes(base64.b64decode(data["b64_json"]))
    elif data.get("url"):
        img = requests.get(data["url"], timeout=60)
        img.raise_for_status()
        output_path.write_bytes(img.content)
    else:
        raise RuntimeError("OpenAI image response missing b64_json and url.")

    return output_path


def generate_voiceover_audio(output_path: Path, script: str) -> Path:
    """Synthesize spoken intro via OpenAI TTS."""
    if not ai_intro_available():
        raise RuntimeError("OPENAI_API_KEY is required for AI intro voiceover.")

    response = requests.post(
        f"{OPENAI_BASE}/audio/speech",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini-tts",
            "voice": REPLAY_INTRO_TTS_VOICE,
            "input": script,
            "response_format": "mp3",
        },
        timeout=60,
    )
    if not response.ok:
        # Fallback to classic tts-1 if mini-tts unavailable
        response = requests.post(
            f"{OPENAI_BASE}/audio/speech",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "tts-1",
                "voice": REPLAY_INTRO_TTS_VOICE,
                "input": script,
            },
            timeout=60,
        )
    if not response.ok:
        raise RuntimeError(
            f"OpenAI TTS failed HTTP {response.status_code}: {response.text[:500]}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return output_path


def _escape_drawtext(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    escaped = escaped.replace("%", r"\%")
    return escaped


def _audio_duration_seconds(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(3.0, float(result.stdout.strip()) + 1.0)


def assemble_ai_intro_video(
    output_path: Path,
    image_path: Path,
    audio_path: Path,
    meeting_title: str,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
) -> Path:
    """Combine AI image + voiceover into an H.264 intro MP4 with title overlay."""
    duration = _audio_duration_seconds(audio_path)
    frames = int(duration * fps)
    title = _escape_drawtext(meeting_title.strip() or BRAND_LINE2)
    brand = _escape_drawtext(BRAND_LINE1)

    video_filter = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='min(1.0+0.0008*on,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={fps},"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.35:t=fill,"
        f"drawtext=text='{brand}':fontcolor=0xc8e6c9:fontsize=64:"
        f"x=(w-text_w)/2:y=h*0.72,"
        f"drawtext=text='{title}':fontcolor=white:fontsize=46:"
        f"x=(w-text_w)/2:y=h*0.80:line_spacing=10,"
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0.0, duration - 0.6)}:d=0.6[v]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-i",
        str(audio_path),
        "-filter_complex",
        video_filter,
        "-map",
        "[v]",
        "-map",
        "1:a",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg AI intro assembly failed:\n{result.stderr[-2000:]}")
    return output_path


def build_ai_intro(
    output_path: str | Path,
    meeting_title: str,
    meeting_date: datetime | None = None,
    work_dir: str | Path | None = None,
) -> Path:
    """
    Generate a full AI intro MP4 for the given meeting.

    Intermediate assets (PNG, MP3) are written to work_dir and kept for debugging.
    """
    output = Path(output_path)
    work = Path(work_dir or output.parent)
    work.mkdir(parents=True, exist_ok=True)

    safe_stem = re.sub(r"[^\w\-]+", "_", meeting_title).strip("_") or "session"
    image_path = work / f".ai_intro_{safe_stem}.png"
    audio_path = work / f".ai_intro_{safe_stem}.mp3"

    script = build_voiceover_script(meeting_title, meeting_date)
    logger.info("AI intro voiceover script: %s", script)

    generate_background_image(image_path, meeting_title)
    generate_voiceover_audio(audio_path, script)
    assemble_ai_intro_video(
        output,
        image_path,
        audio_path,
        meeting_title,
    )
    return output
