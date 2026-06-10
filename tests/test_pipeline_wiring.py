"""Smoke tests for pipeline module wiring (no external API calls)."""

import datetime
import unittest
from unittest.mock import MagicMock, patch

from pipeline import build_title, run_pipeline


SAMPLE_PAYLOAD = {
    "event": "recording.completed",
    "payload": {
        "account_id": "acct-123",
        "object": {
            "topic": "Guild Monthly Webinar",
            "start_time": "2025-05-10T18:00:00Z",
            "duration": 60,
            "recording_files": [
                {
                    "file_type": "MP4",
                    "status": "completed",
                    "download_url": "https://example.com/recording.mp4",
                }
            ],
        },
    },
}


class PipelineWiringTests(unittest.TestCase):
    def test_build_title(self):
        title = build_title("Guild Session", datetime.datetime(2025, 5, 10, tzinfo=datetime.timezone.utc))
        self.assertIn("Guild Session", title)
        self.assertIn("2025", title)

    @patch("pipeline.log_pipeline_result")
    @patch("pipeline.cleanup_files")
    @patch(
        "pipeline.run_wp_post",
        return_value={
            "wp_post_url": "https://ganjierguild.com/replay/test",
            "youtube_url": "https://www.youtube.com/watch?v=abc123",
        },
    )
    @patch("pipeline.run_canva_thumbnail", return_value=None)
    @patch("pipeline.run_youtube_upload", return_value="abc123")
    @patch("pipeline.run_mec_link", return_value=None)
    @patch("pipeline.run_intro", return_value="/tmp/zoom_pipeline/test_trimmed.mp4")
    @patch("pipeline.run_trim", return_value="/tmp/zoom_pipeline/test_trimmed.mp4")
    @patch("pipeline.download_recording", return_value="/tmp/zoom_pipeline/test.mp4")
    @patch("pipeline.os.path.getsize", return_value=1024 * 1024)
    @patch("pipeline.os.path.exists", return_value=True)
    def test_run_pipeline_calls_all_steps(
        self,
        _exists,
        _size,
        mock_download,
        mock_trim,
        mock_intro,
        _mec,
        mock_upload,
        _canva,
        mock_wp,
        _cleanup,
        mock_tracker,
    ):
        run_pipeline(SAMPLE_PAYLOAD)

        mock_download.assert_called_once_with(
            "https://example.com/recording.mp4",
            "/tmp/zoom_pipeline/zoom_20250510_Guild_Monthly_Webinar.mp4",
            account_id="acct-123",
        )
        mock_trim.assert_called_once()
        mock_intro.assert_called_once()
        mock_upload.assert_called_once()
        mock_wp.assert_called_once()
        mock_tracker.assert_called_once()

    @patch("pipeline.TRIM_USE_TRANSCRIPT", False)
    @patch("pipeline.log_pipeline_result")
    @patch("pipeline.cleanup_files")
    @patch(
        "pipeline.run_wp_post",
        return_value={
            "wp_post_url": "https://ganjierguild.com/replay/test",
            "youtube_url": "https://www.youtube.com/watch?v=abc123",
        },
    )
    @patch("pipeline.run_canva_thumbnail", return_value=None)
    @patch("pipeline.run_youtube_upload", return_value="abc123")
    @patch("pipeline.run_mec_link", return_value=None)
    @patch("pipeline.run_intro", return_value="/tmp/zoom_pipeline/test_trimmed.mp4")
    @patch("pipeline.run_trim", return_value="/tmp/zoom_pipeline/test_trimmed.mp4")
    @patch("pipeline.download_recording", return_value="/tmp/zoom_pipeline/test.mp4")
    @patch("pipeline.os.path.getsize", return_value=1024 * 1024)
    @patch("pipeline.os.path.exists", return_value=True)
    def test_run_pipeline_skips_transcript_download_when_disabled(
        self,
        _exists,
        _size,
        mock_download,
        mock_trim,
        _intro,
        _mec,
        _upload,
        _canva,
        _wp,
        _cleanup,
        _tracker,
    ):
        payload = {
            "event": "recording.completed",
            "payload": {
                "account_id": "acct-123",
                "object": {
                    "topic": "Guild Monthly Webinar",
                    "start_time": "2025-05-10T18:00:00Z",
                    "duration": 60,
                    "recording_files": [
                        {
                            "file_type": "MP4",
                            "status": "completed",
                            "download_url": "https://example.com/recording.mp4",
                        },
                        {
                            "file_type": "TRANSCRIPT",
                            "status": "completed",
                            "download_url": "https://example.com/transcript.vtt",
                        },
                    ],
                },
            },
        }

        run_pipeline(payload)

        mock_download.assert_called_once_with(
            "https://example.com/recording.mp4",
            "/tmp/zoom_pipeline/zoom_20250510_Guild_Monthly_Webinar.mp4",
            account_id="acct-123",
        )
        mock_trim.assert_called_once_with(
            "/tmp/zoom_pipeline/zoom_20250510_Guild_Monthly_Webinar.mp4",
            "/tmp/zoom_pipeline/zoom_20250510_Guild_Monthly_Webinar_trimmed.mp4",
            transcript_path=None,
        )


if __name__ == "__main__":
    unittest.main()
