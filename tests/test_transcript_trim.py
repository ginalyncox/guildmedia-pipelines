"""Unit tests for transcript phrase trim detection."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trim_video as trim_video_module
from trim_video import find_phrase_start_sec, parse_vtt_transcript


SAMPLE_VTT = """WEBVTT

1
00:00:05.000 --> 00:00:08.000
Welcome everyone, thanks for joining.

2
00:07:42.500 --> 00:07:46.000
Hope you're having a good session today.

3
00:07:46.000 --> 00:07:50.000
Let's get into the agenda.
"""


class TranscriptTrimTests(unittest.TestCase):
    def test_parse_vtt_transcript(self):
        with tempfile.NamedTemporaryFile("w", suffix=".vtt", delete=False, encoding="utf-8") as fh:
            fh.write(SAMPLE_VTT)
            path = fh.name

        cues = parse_vtt_transcript(path)
        self.assertEqual(len(cues), 3)
        self.assertAlmostEqual(cues[1][0], 462.5)
        self.assertIn("having a good session", cues[1][1].lower())

    def test_find_phrase_start_sec(self):
        cues = parse_vtt_transcript_from_string(SAMPLE_VTT)
        start = find_phrase_start_sec(cues, "having a good session")
        self.assertAlmostEqual(start, 462.5)

    def test_find_phrase_across_adjacent_cues(self):
        vtt = """WEBVTT

1
00:10:00.000 --> 00:10:02.000
We're having a

2
00:10:02.000 --> 00:10:04.000
good session everyone.
"""
        cues = parse_vtt_transcript_from_string(vtt)
        start = find_phrase_start_sec(cues, "having a good session")
        self.assertAlmostEqual(start, 600.0)

    def test_find_phrase_across_three_cues(self):
        vtt = """WEBVTT

1
00:10:00.000 --> 00:10:02.000
We're having

2
00:10:02.000 --> 00:10:04.000
a good

3
00:10:04.000 --> 00:10:06.000
session everyone.
"""
        cues = parse_vtt_transcript_from_string(vtt)
        start = find_phrase_start_sec(cues, "having a good session")
        self.assertAlmostEqual(start, 600.0)

    def test_trim_video_reports_full_after_invalid_range_reset(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "input.mp4"
            output_path = Path(tmp_dir) / "output.mp4"
            input_path.write_bytes(b"input")
            output_path.write_bytes(b"output")

            with (
                patch.object(trim_video_module, "_require_ffmpeg"),
                patch.object(trim_video_module, "get_duration", side_effect=[100.0, 100.0]),
                patch.object(trim_video_module, "detect_start_from_transcript", return_value=50.0),
                patch.object(trim_video_module, "_run_silencedetect", return_value=""),
                patch.object(trim_video_module, "_parse_silence_segments", return_value=[(40.0, 45.0)]),
                patch.object(trim_video_module.subprocess, "run") as mock_run,
            ):
                mock_run.return_value.returncode = 0
                result = trim_video_module.trim_video(
                    str(input_path),
                    str(output_path),
                    transcript_path="transcript.vtt",
                    use_transcript=True,
                )

        self.assertEqual(result["start_method"], "full")
        self.assertAlmostEqual(result["start_sec"], 0.0)
        self.assertAlmostEqual(result["end_sec"], 100.0)


def parse_vtt_transcript_from_string(content: str) -> list[tuple[float, str]]:
    with tempfile.NamedTemporaryFile("w", suffix=".vtt", delete=False, encoding="utf-8") as fh:
        fh.write(content)
        path = fh.name
    return parse_vtt_transcript(path)


if __name__ == "__main__":
    unittest.main()
