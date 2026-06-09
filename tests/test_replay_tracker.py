"""Unit tests for replay_tracker header mapping (no Google API calls)."""

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from replay_tracker import (
    DEFAULT_HEADERS,
    _cmd_headers,
    resolve_column_map,
    spreadsheet_id_from_gsheet_file,
    tracker_backend,
    wp_is_configured,
)


class ReplayTrackerTests(unittest.TestCase):
    def test_resolve_column_map_matches_aliases(self):
        headers = [
            "Session Title",
            "Date",
            "YouTube Link",
            "WP URL",
            "Status",
        ]
        column_map = resolve_column_map(headers)
        self.assertEqual(column_map["topic"], 0)
        self.assertEqual(column_map["date"], 1)
        self.assertEqual(column_map["youtube_url"], 2)
        self.assertEqual(column_map["wp_url"], 3)
        self.assertEqual(column_map["status"], 4)

    def test_resolve_column_map_default_headers(self):
        column_map = resolve_column_map(DEFAULT_HEADERS)
        self.assertEqual(len(column_map), len(DEFAULT_HEADERS))

    def test_tracker_backend_defaults_to_wordpress(self):
        self.assertEqual(tracker_backend(), "wordpress")

    def test_wp_is_configured_when_env_present(self):
        with unittest.mock.patch.dict(
            "os.environ",
            {
                "WP_BASE_URL": "https://example.com",
                "WP_USER": "user",
                "WP_APP_PASSWORD": "pass",
            },
        ):
            self.assertTrue(wp_is_configured())

    def test_spreadsheet_id_from_gsheet_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            shortcut = Path(tmp) / "tracker.gsheet"
            shortcut.write_text(
                json.dumps(
                    {
                        "doc_id": "abc123XYZ",
                        "url": "https://docs.google.com/spreadsheets/d/abc123XYZ/edit",
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(spreadsheet_id_from_gsheet_file(shortcut), "abc123XYZ")

    def test_headers_cli_uses_defaults_without_sheets(self):
        with unittest.mock.patch("replay_tracker.sheets_is_configured", return_value=False):
            with unittest.mock.patch("replay_tracker.read_headers") as read_headers:
                with unittest.mock.patch("builtins.print") as print_mock:
                    self.assertEqual(_cmd_headers(), 0)

        read_headers.assert_not_called()
        print_mock.assert_any_call("Topic")


if __name__ == "__main__":
    unittest.main()
