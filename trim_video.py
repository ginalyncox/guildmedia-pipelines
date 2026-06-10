"""
trim_video.py — Automatically trim a Zoom recording using ffmpeg silence detection.

Silence Detection Logic
-----------------------
Zoom recordings typically begin with tech-setup chatter, a waiting room, or dead air
before the real meeting starts, and end with a few seconds of silence after the last
speaker signs off.

This script uses ffmpeg's `silencedetect` filter to locate those boundaries:

  1. START trimming point:
     - Run silencedetect on the FIRST 10 minutes of the video.
     - Take the LAST silence segment found in that window — its end timestamp is where
       the real meeting content begins (everything before it is pre-meeting noise/waiting).
     - If no silence is detected in the first 10 minutes, start = 0 (keep from beginning).

  2. END trimming point:
     - Run silencedetect on the LAST 10 minutes of the video.
     - Take the FIRST silence segment found in that window — its start timestamp is where
       the meeting effectively ended.
     - If no silence is detected in the last 10 minutes, end = full video duration.

  3. The trimmed clip is written with `-c copy` (stream copy, no re-encode) for a fast,
     lossless cut using `-ss [start] -to [end]`.

Usage
-----
    python trim_video.py input.mp4 output.mp4

Or import and call trim_video() directly from another module.
"""

import os
import re
import subprocess
import sys
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

TRIM_START_PHRASE = os.getenv("TRIM_START_PHRASE", "having a good session").strip()
TRIM_USE_TRANSCRIPT = os.getenv("TRIM_USE_TRANSCRIPT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def _require_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg is not on PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found. Install it and make sure it is on your PATH."
        )


def get_duration(input_path: str) -> float:
    """Return the total duration of a media file in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for '{input_path}':\n{result.stderr.strip()}"
        )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(
            f"Could not parse duration from ffprobe output: {result.stdout!r}"
        )


def _run_silencedetect(
    input_path: str,
    ss: float,
    duration_limit: float,
    noise_db: float = -50.0,
    silence_duration: float = 3.0,
) -> str:
    """
    Run ffmpeg silencedetect on a segment of the file.

    Parameters
    ----------
    input_path      : path to the source video
    ss              : seek offset (seconds) — where to start reading
    duration_limit  : maximum number of seconds to read
    noise_db        : silence threshold in dB  (e.g. -50)
    silence_duration: minimum silence length in seconds (e.g. 3)

    Returns the combined stderr output that contains silencedetect lines.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(ss),
        "-t", str(duration_limit),
        "-i", input_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={silence_duration}",
        "-f", "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # silencedetect output appears in stderr regardless of exit code
    return result.stderr


def _parse_silence_segments(ffmpeg_stderr: str, time_offset: float = 0.0):
    """
    Parse silencedetect output and return a list of (start, end) tuples.

    ffmpeg writes lines like:
        [silencedetect @ ...] silence_start: 12.345
        [silencedetect @ ...] silence_end: 15.678 | silence_duration: 3.333

    Parameters
    ----------
    ffmpeg_stderr : raw stderr text from an ffmpeg silencedetect run
    time_offset   : add this value to every timestamp (used when the ffmpeg
                    seek offset is non-zero, so timestamps are relative to the
                    seek point and must be shifted back to absolute positions)

    Returns a list of (start_sec, end_sec) tuples, sorted by start time.
    """
    starts = [
        float(m) + time_offset
        for m in re.findall(r"silence_start:\s*([\d.]+)", ffmpeg_stderr)
    ]
    ends = [
        float(m) + time_offset
        for m in re.findall(r"silence_end:\s*([\d.]+)", ffmpeg_stderr)
    ]

    segments = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None  # may be missing if video ends mid-silence
        segments.append((s, e))

    return segments


def _vtt_timestamp_to_seconds(timestamp: str) -> float:
    """Convert a VTT timestamp (HH:MM:SS.mmm) to seconds."""
    hours, minutes, rest = timestamp.split(":")
    seconds, millis = rest.split(".")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def parse_vtt_transcript(transcript_path: str) -> list[tuple[float, str]]:
    """
    Parse a Zoom/WebVTT transcript into (start_sec, text) cues.

    Returns cues sorted by start time.
    """
    with open(transcript_path, encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    cues: list[tuple[float, str]] = []
    timestamp_re = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
    )

    for block in re.split(r"\n\s*\n", content.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        timestamp_line = next(
            (line for line in lines if "-->" in line),
            None,
        )
        if not timestamp_line:
            continue

        match = timestamp_re.search(timestamp_line)
        if not match:
            continue

        start_sec = _vtt_timestamp_to_seconds(match.group(1))
        text_lines = [
            line
            for line in lines
            if line != timestamp_line and not line.isdigit() and not line.startswith("WEBVTT")
        ]
        text = " ".join(text_lines).strip()
        if text:
            cues.append((start_sec, text))

    cues.sort(key=lambda cue: cue[0])
    return cues


def find_phrase_start_sec(
    cues: list[tuple[float, str]],
    phrase: str,
) -> float | None:
    """
    Return the timestamp where ``phrase`` first appears in the transcript.

    Matching is case-insensitive substring search on each cue and on short
    multi-cue windows (handles phrases split across VTT lines).
    """
    needle = phrase.strip().lower()
    if not needle or not cues:
        return None

    for start_sec, text in cues:
        if needle in text.lower():
            return start_sec

    # Phrase may span multiple adjacent cues (e.g. one word per VTT cue).
    max_window = max(2, len(needle.split()))
    for idx in range(len(cues)):
        combined_parts: list[str] = []
        for window_end in range(idx, min(len(cues), idx + max_window)):
            combined_parts.append(cues[window_end][1])
            if window_end == idx:
                continue
            if needle in " ".join(combined_parts).lower():
                return cues[idx][0]

    return None


def detect_start_from_transcript(
    transcript_path: str,
    phrase: str | None = None,
) -> float | None:
    """Locate trim start from a Zoom VTT using a spoken marker phrase."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None

    phrase = (phrase or TRIM_START_PHRASE).strip()
    if not phrase:
        return None

    try:
        cues = parse_vtt_transcript(transcript_path)
    except OSError:
        return None

    return find_phrase_start_sec(cues, phrase)


def detect_start_from_silence(
    input_path: str,
    total_duration: float,
    *,
    noise_db: float = -50.0,
    silence_duration: float = 3.0,
    scan_window: float = 600.0,
) -> float:
    """Detect trim start from pre-meeting silence (legacy behavior)."""
    first_window = min(scan_window, total_duration)
    print(f"\nScanning first {first_window:.0f}s for silence (finding meeting start)…")
    stderr_start = _run_silencedetect(
        input_path,
        ss=0.0,
        duration_limit=first_window,
        noise_db=noise_db,
        silence_duration=silence_duration,
    )
    start_segments = _parse_silence_segments(stderr_start, time_offset=0.0)

    if start_segments:
        last_silence = start_segments[-1]
        start_sec = last_silence[1] if last_silence[1] is not None else last_silence[0]
        print(f"  Found {len(start_segments)} silence segment(s) in first window.")
        print(f"  Last silence: {last_silence[0]:.2f}s → {last_silence[1]:.2f}s")
        print(f"  → Trim START set to: {start_sec:.2f}s")
        return start_sec

    print("  No silence found in first window → START defaults to 0.0s")
    return 0.0


# ── main logic ────────────────────────────────────────────────────────────────

def trim_video(
    input_path: str,
    output_path: str,
    noise_db: float = -50.0,
    silence_duration: float = 3.0,
    scan_window: float = 600.0,  # 10 minutes
    transcript_path: str | None = None,
    start_phrase: str | None = None,
    use_transcript: bool | None = None,
) -> dict:
    """
    Trim a Zoom recording using transcript phrase detection and/or silence.

    Parameters
    ----------
    input_path       : path to the source MP4 file
    output_path      : path where the trimmed MP4 will be written
    noise_db         : silence threshold in dB (default -50)
    silence_duration : minimum silence length to detect in seconds (default 3)
    scan_window      : how many seconds to scan at each end (default 600 = 10 min)
    transcript_path: optional Zoom VTT transcript for phrase-based trim start
    start_phrase     : phrase marking session start (default TRIM_START_PHRASE env)
    use_transcript   : try transcript trim first (default TRIM_USE_TRANSCRIPT env)

    Returns
    -------
    dict with keys:
        start_sec    — detected (or default) trim start in seconds
        end_sec      — detected (or default) trim end in seconds
        duration_sec — duration of the output clip
        output_path  — path to the written output file
        start_method — "transcript", "silence", or "full"
    """
    # ── validate inputs ───────────────────────────────────────────────────────
    _require_ffmpeg()

    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: '{input_path}'")

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir and not os.path.isdir(output_dir):
        raise FileNotFoundError(f"Output directory does not exist: '{output_dir}'")

    total_duration = get_duration(input_path)
    print(f"Input  : {input_path}")
    print(f"Total duration: {total_duration:.2f}s ({total_duration/60:.1f} min)")

    phrase = (start_phrase if start_phrase is not None else TRIM_START_PHRASE).strip()
    try_transcript = TRIM_USE_TRANSCRIPT if use_transcript is None else use_transcript
    start_method = "silence"
    start_sec: float

    if try_transcript and phrase and transcript_path:
        print(f"\nSearching transcript for start phrase: {phrase!r}")
        transcript_start = detect_start_from_transcript(transcript_path, phrase)
        if transcript_start is not None:
            start_sec = transcript_start
            start_method = "transcript"
            print(f"  → Trim START set from transcript: {start_sec:.2f}s")
        else:
            print("  Phrase not found in transcript — falling back to silence detection.")
            start_sec = detect_start_from_silence(
                input_path,
                total_duration,
                noise_db=noise_db,
                silence_duration=silence_duration,
                scan_window=scan_window,
            )
    else:
        if try_transcript and phrase and not transcript_path:
            print("\nNo transcript file available — using silence detection for trim start.")
        start_sec = detect_start_from_silence(
            input_path,
            total_duration,
            noise_db=noise_db,
            silence_duration=silence_duration,
            scan_window=scan_window,
        )

    # ── detect END (first silence in last scan_window) ────────────────────────
    last_window_offset = max(0.0, total_duration - scan_window)
    last_window_len = total_duration - last_window_offset
    print(f"\nScanning last {last_window_len:.0f}s (from {last_window_offset:.2f}s) for silence (finding meeting end)…")
    stderr_end = _run_silencedetect(
        input_path, ss=last_window_offset, duration_limit=last_window_len,
        noise_db=noise_db, silence_duration=silence_duration,
    )
    end_segments = _parse_silence_segments(stderr_end, time_offset=last_window_offset)

    if end_segments:
        # Use the START of the FIRST silence segment in this window as the trim end
        first_silence = end_segments[0]
        end_sec: float = first_silence[0]
        print(f"  Found {len(end_segments)} silence segment(s) in last window.")
        print(f"  First silence starts at: {first_silence[0]:.2f}s")
        print(f"  → Trim END set to: {end_sec:.2f}s")
    else:
        end_sec = total_duration
        print(f"  No silence found in last window → END defaults to full duration ({total_duration:.2f}s)")

    # ── sanity check ─────────────────────────────────────────────────────────
    if start_sec >= end_sec:
        print(
            f"\nWARNING: Detected start ({start_sec:.2f}s) >= end ({end_sec:.2f}s). "
            "Falling back to full video (no trim)."
        )
        start_sec = 0.0
        end_sec = total_duration
        start_method = "full"

    clip_duration = end_sec - start_sec
    print(f"\nTrim range : {start_sec:.2f}s → {end_sec:.2f}s  ({clip_duration:.2f}s / {clip_duration/60:.1f} min)")

    # ── run the actual trim ───────────────────────────────────────────────────
    print(f"\nWriting trimmed file to: {output_path}")
    trim_cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_sec),
        "-to", str(end_sec),
        "-i", input_path,
        "-c", "copy",
        output_path,
    ]
    result = subprocess.run(trim_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg trim failed (exit {result.returncode}):\n{result.stderr[-2000:]}"
        )

    # ── verify output and report ──────────────────────────────────────────────
    if not os.path.isfile(output_path):
        raise RuntimeError(f"ffmpeg ran successfully but output file not found: '{output_path}'")

    actual_duration = get_duration(output_path)
    print(f"\nDone.")
    print(f"  Start    : {start_sec:.2f}s")
    print(f"  End      : {end_sec:.2f}s")
    print(f"  Duration : {actual_duration:.2f}s ({actual_duration/60:.1f} min)")
    print(f"  Output   : {output_path}")

    return {
        "start_sec": start_sec,
        "end_sec": end_sec,
        "duration_sec": actual_duration,
        "output_path": output_path,
        "start_method": start_method,
    }


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python trim_video.py <input.mp4> <output.mp4>")
        print("       [--noise <dB>] [--silence-duration <sec>] [--scan-window <sec>]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    # Optional overrides via simple positional-style flags
    kwargs: dict = {}
    args = sys.argv[3:]
    i = 0
    while i < len(args):
        flag = args[i]
        if flag == "--noise" and i + 1 < len(args):
            kwargs["noise_db"] = float(args[i + 1])
            i += 2
        elif flag == "--silence-duration" and i + 1 < len(args):
            kwargs["silence_duration"] = float(args[i + 1])
            i += 2
        elif flag == "--scan-window" and i + 1 < len(args):
            kwargs["scan_window"] = float(args[i + 1])
            i += 2
        else:
            print(f"Unknown argument: {flag}")
            sys.exit(1)

    try:
        result = trim_video(input_path, output_path, **kwargs)
        sys.exit(0)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
