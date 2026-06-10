"""Unit tests for transcript phrase trim detection."""

import tempfile
import unittest
from pathlib import Path

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


def parse_vtt_transcript_from_string(content: str) -> list[tuple[float, str]]:
    with tempfile.NamedTemporaryFile("w", suffix=".vtt", delete=False, encoding="utf-8") as fh:
        fh.write(content)
        path = fh.name
    return parse_vtt_transcript(path)


if __name__ == "__main__":
    unittest.main()
