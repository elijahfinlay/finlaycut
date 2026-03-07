import unittest
from unittest.mock import patch

from services.resolve_service import ResolveService


class ResolveServiceStatusTests(unittest.TestCase):
    def _make_service(self) -> ResolveService:
        service = ResolveService()
        service._connected = True
        service._resolve = object()
        service._last_project_info = {
            "project_name": "Pinned Project",
            "timeline_count": 3,
            "current_timeline": {
                "name": "Timeline A",
                "duration": 120.0,
                "start_frame": 100,
                "end_frame": 2980,
                "video_tracks": 2,
                "audio_tracks": 1,
                "subtitle_tracks": 0,
                "framerate": "24",
                "resolution": "1920x1080",
            },
        }
        return service

    def test_get_status_uses_cached_project_info_when_lookup_returns_none(self):
        service = self._make_service()

        with patch("services.resolve_service.get_project_info", return_value=None):
            status = service.get_status()

        self.assertTrue(status["connected"])
        self.assertTrue(status["using_cached"])
        self.assertEqual(status["project"]["name"], "Pinned Project")
        self.assertEqual(status["timeline"]["name"], "Timeline A")

    def test_get_status_uses_cached_project_info_when_lookup_raises(self):
        service = self._make_service()

        with patch("services.resolve_service.get_project_info", side_effect=RuntimeError("Resolve busy")):
            status = service.get_status()

        self.assertTrue(status["connected"])
        self.assertTrue(status["using_cached"])
        self.assertEqual(status["project"]["name"], "Pinned Project")
        self.assertEqual(status["timeline"]["name"], "Timeline A")

    def test_get_status_reports_disconnected_without_cache(self):
        service = ResolveService()
        service._connected = True
        service._resolve = object()

        with patch("services.resolve_service.get_project_info", side_effect=RuntimeError("Resolve offline")):
            status = service.get_status()

        self.assertFalse(status["connected"])
        self.assertFalse(status["using_cached"])
        self.assertIsNone(status["project"])
        self.assertIsNone(status["timeline"])


if __name__ == "__main__":
    unittest.main()
