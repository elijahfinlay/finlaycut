import unittest

from services.resolve_service import ResolveService


class FakeTimeline:
    def __init__(self, audio_track_count: int, video_track_count: int):
        self._counts = {
            "audio": audio_track_count,
            "video": video_track_count,
        }

    def GetTrackCount(self, media_type: str) -> int:
        return self._counts.get(media_type, 0)


class ResolveServiceAudioTrackSelectionTests(unittest.TestCase):
    def _make_service(self, timeline, tracks: dict[tuple[str, int], list[dict]]) -> ResolveService:
        service = ResolveService()
        service.get_current_timeline = lambda: timeline
        service.get_timeline_clips = lambda media_type, track_index: tracks.get((media_type, track_index), [])
        service.get_clip_audio_context = lambda item: item.get("context")
        service.get_clip_file_path = lambda item: item.get("file_path")
        return service

    def test_prefers_track_with_most_usable_clip_contexts(self):
        timeline = FakeTimeline(audio_track_count=1, video_track_count=1)
        tracks = {
            ("audio", 1): [
                {"file_path": None, "context": None},
                {"file_path": None, "context": None},
            ],
            ("video", 1): [
                {"file_path": "/tmp/a.mov", "context": {"file_path": "/tmp/a.mov"}},
                {"file_path": "/tmp/b.mov", "context": {"file_path": "/tmp/b.mov"}},
            ],
        }
        service = self._make_service(timeline, tracks)

        result = service.get_timeline_audio_clip_contexts()

        self.assertEqual(result["selected_media_type"], "video")
        self.assertEqual(result["selected_track_index"], 1)
        self.assertEqual(result["usable_count"], 2)
        self.assertEqual(len(result["contexts"]), 2)

    def test_reports_attempts_when_no_track_has_usable_contexts(self):
        timeline = FakeTimeline(audio_track_count=2, video_track_count=1)
        tracks = {
            ("audio", 1): [{"file_path": None, "context": None}],
            ("audio", 2): [{"file_path": None, "context": None}],
            ("video", 1): [{"file_path": None, "context": None}],
        }
        service = self._make_service(timeline, tracks)

        result = service.get_timeline_audio_clip_contexts()

        self.assertTrue(result["has_any_items"])
        self.assertEqual(result["contexts"], [])
        self.assertIsNone(result["selected_media_type"])
        self.assertEqual(len(result["attempts"]), 3)


if __name__ == "__main__":
    unittest.main()
