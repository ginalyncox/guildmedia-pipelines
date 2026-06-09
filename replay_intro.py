"""
replay_intro.py — Build and prepend a branded YouTube intro for replay videos.

The default intro is generated with ffmpeg (no external design files required).
You can replace it later with a custom MP4 from Canva or a video editor by setting
REPLAY_INTRO_PATH in .env.

Usage:
    python replay_intro.py --build --title "Guild Monthly Webinar"
    python replay_intro.py --prepend trimmed.mp4 --title "Guild Monthly Webinar" -o final.mp4
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent.resolve()
ASSETS_DIR = SCRIPT_DIR / "assets"
DEFAULT_INTRO_PATH = ASSETS_DIR / "replay_intro_template.mp4"

load_dotenv(SCRIPT_DIR / ".env")

REPLAY_INTRO_ENABLED = os.getenv("REPLAY_INTRO_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REPLAY_INTRO_PATH = os.getenv("REPLAY_INTRO_PATH", "").strip()
REPLAY_INTRO_DURATION = float(os.getenv("REPLAY_INTRO_DURATION", "5"))
REPLAY_INTRO_DYNAMIC_TITLE = os.getenv("REPLAY_INTRO_DYNAMIC_TITLE", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REPLAY_INTRO_WIDTH = int(os.getenv("REPLAY_INTRO_WIDTH", "1920"))
REPLAY_INTRO_HEIGHT = int(os.getenv("REPLAY_INTRO_HEIGHT", "1080"))
REPLAY_INTRO_FPS = int(os.getenv("REPLAY_INTRO_FPS", "30"))

BRAND_BG_COLOR = os.getenv("REPLAY_INTRO_BG_COLOR", "0x0f3d24")
BRAND_ACCENT_COLOR = os.getenv("REPLAY_INTRO_ACCENT_COLOR", "0xc8e6c9")
BRAND_LINE1 = os.getenv("REPLAY_INTRO_LINE1", "Ganjier Guild")
BRAND_LINE2 = os.getenv("REPLAY_INTRO_LINE2", "Replay Library")


def _require_ffmpeg() -> None:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg not found. Install ffmpeg and ensure it is on PATH.") from exc


def _escape_drawtext(value: str) -> str:
    """Escape text for ffmpeg drawtext filter values."""
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace("\n", r"\n")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", r"\'")
    escaped = escaped.replace("%", r"\%")
    return escaped


def _wrap_title(title: str, max_chars: int = 42) -> str:
    words = title.split()
    if not words:
        return BRAND_LINE2

    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if len(candidate) <= max_chars:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines[:2])


def intro_enabled() -> bool:
    return REPLAY_INTRO_ENABLED


def resolve_static_intro_path() -> Path | None:
    if REPLAY_INTRO_PATH:
        path = Path(REPLAY_INTRO_PATH)
        if not path.is_absolute():
            path = SCRIPT_DIR / path
        return path if path.exists() else None
    if DEFAULT_INTRO_PATH.exists():
        return DEFAULT_INTRO_PATH
    return None


def build_intro(
    output_path: str | Path,
    meeting_title: str | None = None,
    duration: float | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
) -> Path:
    """
    Generate a branded intro MP4 with ffmpeg.

    Parameters
    ----------
    output_path : str or Path
        Destination MP4 path.
    meeting_title : str, optional
        Meeting topic shown on the intro. When omitted, only brand lines are shown.
    duration : float, optional
        Intro length in seconds (default from REPLAY_INTRO_DURATION).
    """
    _require_ffmpeg()

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    duration = duration if duration is not None else REPLAY_INTRO_DURATION
    width = width if width is not None else REPLAY_INTRO_WIDTH
    height = height if height is not None else REPLAY_INTRO_HEIGHT
    fps = fps if fps is not None else REPLAY_INTRO_FPS

    line1 = _escape_drawtext(BRAND_LINE1)
    line2 = _escape_drawtext(BRAND_LINE2)
    title_block = ""
    if meeting_title and REPLAY_INTRO_DYNAMIC_TITLE:
        wrapped = _escape_drawtext(_wrap_title(meeting_title.strip()))
        title_block = (
            f",drawtext=text='{wrapped}':fontcolor=white:fontsize=52:"
            f"x=(w-text_w)/2:y=(h/2)+30:line_spacing=12:"
            f"alpha='if(lt(t,0.6),t/0.6,if(lt(t,{duration - 0.6}),1,({duration}-t)/0.6))'"
        )

    fade_expr = (
        f"if(lt(t,0.6),t/0.6,if(lt(t,{duration - 0.6}),1,({duration}-t)/0.6))"
    )
    video_filter = (
        f"color=c={BRAND_BG_COLOR}:s={width}x{height}:d={duration}:r={fps},"
        f"drawtext=text='{line1}':fontcolor={BRAND_ACCENT_COLOR}:fontsize=78:"
        f"x=(w-text_w)/2:y=(h/2)-120:alpha='{fade_expr}',"
        f"drawtext=text='{line2}':fontcolor=white:fontsize=42:"
        f"x=(w-text_w)/2:y=(h/2)-35:alpha='{fade_expr}'"
        f"{title_block}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        video_filter,
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=48000:cl=stereo:d={duration}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg intro build failed:\n{result.stderr[-2000:]}")

    if not output.exists():
        raise RuntimeError(f"Intro build did not produce output: {output}")

    return output


def prepend_intro(
    body_path: str | Path,
    output_path: str | Path,
    intro_path: str | Path | None = None,
    meeting_title: str | None = None,
) -> Path:
    """
    Prepend an intro clip to a trimmed replay and write a YouTube-ready MP4.

    If intro_path is omitted, a static intro from REPLAY_INTRO_PATH / assets/ is used.
    When REPLAY_INTRO_DYNAMIC_TITLE is enabled and meeting_title is provided, a fresh
    per-meeting intro is generated into a temp file instead.
    """
    _require_ffmpeg()

    body = Path(body_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not body.is_file():
        raise FileNotFoundError(f"Body video not found: {body}")

    generated_intro: Path | None = None
    intro = Path(intro_path) if intro_path else resolve_static_intro_path()

    if intro is None or (meeting_title and REPLAY_INTRO_DYNAMIC_TITLE):
        generated_intro = output.parent / f".intro_{body.stem}.mp4"
        intro = build_intro(generated_intro, meeting_title=meeting_title)
    elif not intro.is_file():
        raise FileNotFoundError(f"Intro video not found: {intro}")

    width = REPLAY_INTRO_WIDTH
    height = REPLAY_INTRO_HEIGHT
    fps = REPLAY_INTRO_FPS

    filter_complex = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v0];"
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}[v1];"
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a0];"
        f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo[a1];"
        f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(intro),
        "-i",
        str(body),
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if generated_intro and generated_intro.exists():
        generated_intro.unlink()

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg intro prepend failed:\n{result.stderr[-2000:]}")

    if not output.exists():
        raise RuntimeError(f"Intro prepend did not produce output: {output}")

    return output


def prepare_upload_video(
    trimmed_path: str | Path,
    meeting_title: str,
    output_path: str | Path | None = None,
) -> Path:
    """
    Return the video path that should be uploaded to YouTube.

    When intro is disabled, returns trimmed_path unchanged.
    When enabled, prepends the intro and returns the new output path.
    """
    trimmed = Path(trimmed_path)
    if not intro_enabled():
        return trimmed

    if output_path is None:
        output_path = trimmed.with_name(f"{trimmed.stem}_with_intro{trimmed.suffix}")
    return prepend_intro(trimmed, output_path, meeting_title=meeting_title)


def _cmd_build(args: argparse.Namespace) -> int:
    path = build_intro(
        args.output,
        meeting_title=args.title,
        duration=args.duration,
    )
    print(f"Intro written to: {path}")
    return 0


def _cmd_prepend(args: argparse.Namespace) -> int:
    path = prepend_intro(
        args.input,
        args.output,
        intro_path=args.intro,
        meeting_title=args.title,
    )
    print(f"Video with intro written to: {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Ganjier Guild replay intro builder")
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="Generate a standalone intro MP4")
    build_parser.add_argument("--title", default="", help="Meeting title shown on intro")
    build_parser.add_argument(
        "--output",
        default=str(DEFAULT_INTRO_PATH),
        help="Output MP4 path",
    )
    build_parser.add_argument("--duration", type=float, default=None)
    build_parser.set_defaults(func=_cmd_build)

    prepend_parser = sub.add_parser("prepend", help="Prepend intro to a trimmed replay")
    prepend_parser.add_argument("input", help="Trimmed replay MP4")
    prepend_parser.add_argument("-o", "--output", required=True, help="Output MP4 path")
    prepend_parser.add_argument("--intro", default=None, help="Optional static intro MP4")
    prepend_parser.add_argument("--title", default="", help="Meeting title for dynamic intro")
    prepend_parser.set_defaults(func=_cmd_prepend)

    args = parser.parse_args()
    try:
        raise SystemExit(args.func(args))
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
