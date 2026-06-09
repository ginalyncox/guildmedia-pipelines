"""Unit tests for MEC title matching helpers."""

import unittest

from mec_events import normalize_title, title_score


class MecEventsTests(unittest.TestCase):
    def test_title_score_exact_substring(self):
        score = title_score(
            "All Hands On Deck",
            "Ganjier Guild – All Hands On Deck",
        )
        self.assertEqual(score, 100)

    def test_title_score_partial_overlap(self):
        score = title_score("Guild Monthly Webinar", "Guild Monthly Session")
        self.assertGreater(score, 0)

    def test_normalize_title(self):
        self.assertEqual(
            normalize_title("Ganjier Guild – All Hands On Deck"),
            "ganjier guild all hands on deck",
        )


if __name__ == "__main__":
    unittest.main()
