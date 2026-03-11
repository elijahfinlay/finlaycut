import unittest
from unittest.mock import patch

from services.resolve_service import ResolveService


class _FakeTimeline:
    def __init__(self, name: str):
        self._name = name

    def GetName(self):
        return self._name

    def GetStartFrame(self):
        return 100

    def GetEndFrame(self):
        return 2980

    def GetTrackCount(self, track_type: str):
        return {
            "video": 2,
            "audio": 1,
            "subtitle": 0,
        }[track_type]

    def GetSetting(self, key: str):
        return {
            "timelineFrameRate": "24",
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
        }[key]


class _FakeProject:
    def __init__(self, timelines):
        self._timelines = timelines

    def GetName(self):
        return "Pinned Project"

    def GetCurrentTimeline(self):
        return None

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index: int):
        return self._timelines[index - 1]


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

    def test_connected_retries_when_resolve_handle_exists_but_flag_dropped(self):
        service = ResolveService()
        service._connected = False
        service._resolve = object()

        with patch.object(service, "try_connect", return_value=True) as try_connect:
            self.assertTrue(service.connected)

        try_connect.assert_called_once()

    def test_get_current_timeline_falls_back_to_cached_timeline_name(self):
        service = self._make_service()
        project = _FakeProject([
            _FakeTimeline("Timeline Z"),
            _FakeTimeline("Timeline A"),
        ])

        with patch.object(service, "get_current_project", return_value=project):
            timeline = service.get_current_timeline()

        self.assertIsNotNone(timeline)
        self.assertEqual(timeline.GetName(), "Timeline A")

    def test_get_status_uses_recovered_timeline_when_resolve_returns_none(self):
        service = self._make_service()
        recovered_timeline = _FakeTimeline("Timeline A")

        with (
            patch(
                "services.resolve_service.get_project_info",
                return_value={
                    "project_name": "Pinned Project",
                    "timeline_count": 3,
                    "current_timeline": None,
                },
            ),
            patch.object(service, "get_current_timeline", return_value=recovered_timeline),
        ):
            status = service.get_status()

        self.assertTrue(status["connected"])
        self.assertTrue(status["using_cached"])
        self.assertEqual(status["timeline"]["name"], "Timeline A")


if __name__ == "__main__":
    unittest.main()
