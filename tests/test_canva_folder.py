"""Unit tests for Canva folder resolution (no interactive OAuth)."""

import unittest
from unittest.mock import patch

from canva_thumbnail import resolve_thumbnail_folder_id


class CanvaFolderResolutionTests(unittest.TestCase):
    @patch("canva_thumbnail.CANVA_CLIENT_ID", "test-client")
    @patch("canva_thumbnail.CANVA_THUMBNAIL_FOLDER_NAME", "Replay Thumbnail Folder")
    @patch("canva_thumbnail.CANVA_THUMBNAIL_FOLDER_ID", "FOLDER123")
    @patch("canva_thumbnail.get_access_token_if_available", return_value=None)
    @patch("canva_thumbnail.get_folder_id_by_name")
    def test_uses_folder_id_when_no_token(self, mock_lookup, _mock_token):
        folder_id = resolve_thumbnail_folder_id()
        self.assertEqual(folder_id, "FOLDER123")
        mock_lookup.assert_not_called()

    @patch("canva_thumbnail.CANVA_CLIENT_ID", "test-client")
    @patch("canva_thumbnail.CANVA_THUMBNAIL_FOLDER_NAME", "Replay Thumbnail Folder")
    @patch("canva_thumbnail.CANVA_THUMBNAIL_FOLDER_ID", "FOLDER123")
    @patch("canva_thumbnail.get_access_token_if_available", return_value="token")
    @patch("canva_thumbnail.get_folder_id_by_name", return_value="NAMED456")
    def test_prefers_name_lookup_when_token_available(self, mock_lookup, _mock_token):
        folder_id = resolve_thumbnail_folder_id()
        self.assertEqual(folder_id, "NAMED456")

    @patch("canva_thumbnail.CANVA_CLIENT_ID", "test-client")
    @patch("canva_thumbnail.CANVA_THUMBNAIL_FOLDER_NAME", "Replay Thumbnail Folder")
    @patch("canva_thumbnail.CANVA_THUMBNAIL_FOLDER_ID", "FOLDER123")
    @patch("canva_thumbnail.get_access_token_if_available", return_value="token")
    @patch(
        "canva_thumbnail.get_folder_id_by_name",
        side_effect=RuntimeError("OAuth timeout"),
    )
    def test_falls_back_to_folder_id_on_lookup_error(self, mock_lookup, _mock_token):
        folder_id = resolve_thumbnail_folder_id()
        self.assertEqual(folder_id, "FOLDER123")


if __name__ == "__main__":
    unittest.main()
