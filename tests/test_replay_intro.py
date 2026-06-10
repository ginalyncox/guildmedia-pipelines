"""Unit tests for replay_intro helpers (no ffmpeg required)."""

import unittest

from replay_intro import _escape_drawtext, _wrap_title, intro_enabled


class ReplayIntroTests(unittest.TestCase):
    def test_escape_drawtext_handles_special_chars(self):
        self.assertEqual(_escape_drawtext("Topic: A & B"), r"Topic\: A & B")

    def test_wrap_title_splits_long_lines(self):
        wrapped = _wrap_title("Guild Monthly Webinar for Product Specialists and Students")
        self.assertIn("\n", wrapped)

    def test_intro_disabled_by_default(self):
        self.assertFalse(intro_enabled())


if __name__ == "__main__":
    unittest.main()
