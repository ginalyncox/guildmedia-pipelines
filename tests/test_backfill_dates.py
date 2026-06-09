"""Unit tests for backfill date range resolution."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backfill import (
    local_yesterday_window,
    recording_start_in_window,
    resolve_date_range,
    zoom_date_strings_for_utc_window,
)


class BackfillDateTests(unittest.TestCase):
    def test_yesterday_only_expands_utc_range_for_chicago(self):
        fixed = datetime(2026, 6, 9, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
        with patch("backfill.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            start, end = resolve_date_range(yesterday_only=True)
        self.assertEqual(start, "2026-06-08")
        self.assertEqual(end, "2026-06-09")

    def test_local_yesterday_window_covers_evening_central_sessions(self):
        fixed = datetime(2026, 6, 9, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
        with patch("backfill.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            window_start, window_end = local_yesterday_window()

        # Monday 7pm CT is Tuesday 00:00 UTC during CDT.
        evening_session = "2026-06-09T00:00:00Z"
        self.assertTrue(
            recording_start_in_window(evening_session, window_start, window_end)
        )

    def test_local_yesterday_window_excludes_prior_utc_day(self):
        fixed = datetime(2026, 6, 9, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
        with patch("backfill.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            window_start, window_end = local_yesterday_window()

        # Monday 2am UTC is still Sunday evening in Central.
        prior_session = "2026-06-08T02:00:00Z"
        self.assertFalse(
            recording_start_in_window(prior_session, window_start, window_end)
        )

    def test_zoom_date_strings_span_two_utc_days_for_local_day(self):
        window_start = datetime(2026, 6, 8, 5, 0, tzinfo=timezone.utc)
        window_end = datetime(2026, 6, 9, 5, 0, tzinfo=timezone.utc)
        start, end = zoom_date_strings_for_utc_window(window_start, window_end)
        self.assertEqual(start, "2026-06-08")
        self.assertEqual(end, "2026-06-09")

    def test_explicit_date_range(self):
        start, end = resolve_date_range(
            from_date="2026-06-08",
            to_date="2026-06-08",
        )
        self.assertEqual(start, "2026-06-08")
        self.assertEqual(end, "2026-06-08")


if __name__ == "__main__":
    unittest.main()
