import unittest

from services.resolve_service import ResolveService


class _FakeItem:
    def __init__(self, name: str):
        self.name = name


class _FakeTimeline:
    def __init__(self, name: str):
        self._name = name

    def GetName(self):
        return self._name


class _FakeProject:
    def __init__(self):
        self.timelines = [_FakeTimeline("Timeline A"), _FakeTimeline("Timeline B")]
        self.current_timeline = self.timelines[0]

    def GetTimelineByIndex(self, index: int):
        if 1 <= index <= len(self.timelines):
            return self.timelines[index - 1]
        return None

    def GetCurrentTimeline(self):
        return self.current_timeline

    def SetCurrentTimeline(self, timeline):
        self.current_timeline = timeline
        return True


class ResolveServiceVideoContextTests(unittest.TestCase):
    def test_get_timeline_video_clip_contexts_filters_to_requested_range(self):
        service = ResolveService()
        clip_a = _FakeItem("Clip A")
        clip_b = _FakeItem("Clip B")
        clip_c = _FakeItem("Clip C")
        contexts = {
            clip_a: {
                "clip_start_frame": 0,
                "clip_end_frame": 100,
                "clip_duration_seconds": 4.0,
                "clip_timeline_offset": 0.0,
                "clip_item_id": "a",
                "file_path": "/tmp/a.mov",
                "source_start_frame": 0,
                "source_end_frame": 100,
            },
            clip_b: {
                "clip_start_frame": 100,
                "clip_end_frame": 200,
                "clip_duration_seconds": 4.0,
                "clip_timeline_offset": 4.0,
                "clip_item_id": "b",
                "file_path": "/tmp/b.mov",
                "source_start_frame": 100,
                "source_end_frame": 200,
            },
            clip_c: {
                "clip_start_frame": 210,
                "clip_end_frame": 260,
                "clip_duration_seconds": 2.0,
                "clip_timeline_offset": 8.75,
                "clip_item_id": "c",
                "file_path": "/tmp/c.mov",
                "source_start_frame": 210,
                "source_end_frame": 260,
            },
        }

        service.get_current_timeline = lambda: object()  # type: ignore[method-assign]
        service.get_timeline_clips = lambda track_type="video", track_index=1: [clip_a, clip_b, clip_c]  # type: ignore[method-assign]
        service.get_clip_audio_context = lambda item: dict(contexts[item])  # type: ignore[method-assign]
        service.get_timeline_item_name = lambda item: item.name  # type: ignore[method-assign]

        result = service.get_timeline_video_clip_contexts(track_index=2, in_frame=90, out_frame=210)

        self.assertEqual(result["clip_count"], 3)
        self.assertEqual(result["usable_count"], 2)
        self.assertEqual([ctx["clip_name"] for ctx in result["contexts"]], ["Clip A", "Clip B"])
        self.assertTrue(all(ctx["track_type"] == "video" for ctx in result["contexts"]))
        self.assertTrue(all(ctx["track_index"] == 2 for ctx in result["contexts"]))

    def test_set_current_timeline_by_index_switches_active_timeline(self):
        service = ResolveService()
        project = _FakeProject()
        service.get_current_project = lambda: project  # type: ignore[method-assign]

        result = service.set_current_timeline_by_index(2)

        self.assertTrue(result["success"])
        self.assertEqual(result["timeline_index"], 2)
        self.assertEqual(result["timeline_name"], "Timeline B")
        self.assertEqual(project.GetCurrentTimeline().GetName(), "Timeline B")


if __name__ == "__main__":
    unittest.main()
