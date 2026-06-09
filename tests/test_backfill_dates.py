"""Unit tests for backfill date range resolution."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backfill import filter_recordings, resolve_date_range


class BackfillDateTests(unittest.TestCase):
    def test_yesterday_only_expands_to_utc_dates_covering_chicago_day(self):
        fixed = datetime(2026, 6, 9, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
        with patch("backfill.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            start, end = resolve_date_range(yesterday_only=True)
        self.assertEqual(start, "2026-06-08")
        self.assertEqual(end, "2026-06-09")

    def test_explicit_single_day_expands_to_utc_dates_covering_chicago_day(self):
        start, end = resolve_date_range(
            from_date="2026-06-08",
            to_date="2026-06-08",
        )
        self.assertEqual(start, "2026-06-08")
        self.assertEqual(end, "2026-06-09")

    def test_filter_recordings_keeps_late_evening_chicago_session(self):
        tz = ZoneInfo("America/Chicago")
        window_start = datetime(2026, 6, 8, 0, 0, tzinfo=tz)
        window = (window_start, window_start + timedelta(days=1))

        def meeting(uuid: str, start_time: str) -> dict:
            return {
                "uuid": uuid,
                "topic": "Guild Replay",
                "start_time": start_time,
                "recording_files": [{"file_type": "MP4", "status": "completed"}],
            }

        recordings = filter_recordings(
            [
                meeting("previous-local-day", "2026-06-08T04:30:00Z"),
                meeting("expected-day", "2026-06-08T18:00:00Z"),
                meeting("late-evening", "2026-06-09T00:30:00Z"),
                meeting("next-local-day", "2026-06-09T12:00:00Z"),
            ],
            window,
        )

        self.assertEqual(
            [recording["uuid"] for recording in recordings],
            ["expected-day", "late-evening"],
        )


if __name__ == "__main__":
    unittest.main()
