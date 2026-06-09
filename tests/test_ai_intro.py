"""Unit tests for AI intro duration handling."""

import unittest
from pathlib import Path
from unittest.mock import patch

from ai_intro import _audio_duration_seconds


class AiIntroDurationTests(unittest.TestCase):
    @patch("ai_intro.subprocess.run")
    def test_audio_duration_not_capped_at_twelve_seconds(self, mock_run):
        mock_run.return_value.stdout = "15.5\n"
        duration = _audio_duration_seconds(Path("voiceover.mp3"))
        self.assertAlmostEqual(duration, 16.5)

    @patch("ai_intro.subprocess.run")
    def test_audio_duration_has_minimum_floor(self, mock_run):
        mock_run.return_value.stdout = "1.2\n"
        duration = _audio_duration_seconds(Path("voiceover.mp3"))
        self.assertEqual(duration, 3.0)


if __name__ == "__main__":
    unittest.main()
