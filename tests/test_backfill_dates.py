"""Unit tests for backfill date range resolution."""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backfill import resolve_date_range


class BackfillDateTests(unittest.TestCase):
    def test_yesterday_only_uses_chicago_timezone(self):
        fixed = datetime(2026, 6, 9, 15, 0, tzinfo=ZoneInfo("America/Chicago"))
        with patch("backfill.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            start, end = resolve_date_range(yesterday_only=True)
        self.assertEqual(start, "2026-06-08")
        self.assertEqual(end, "2026-06-08")

    def test_explicit_date_range(self):
        start, end = resolve_date_range(
            from_date="2026-06-08",
            to_date="2026-06-08",
        )
        self.assertEqual(start, "2026-06-08")
        self.assertEqual(end, "2026-06-08")


if __name__ == "__main__":
    unittest.main()
