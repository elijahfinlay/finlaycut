import unittest

from services.resolve_service import ResolveService


class _FakeMediaPoolItem:
    def __init__(self, unique_id: str):
        self._unique_id = unique_id

    def GetUniqueId(self):
        return self._unique_id


class _FakeTimelineItem:
    def __init__(self, unique_id: str):
        self._media_pool_item = _FakeMediaPoolItem(unique_id)

    def GetMediaPoolItem(self):
        return self._media_pool_item


class _FakeTimeline:
    def __init__(self):
        self.calls: list[list[object]] = []

    def SetClipsLinked(self, items, should_link):
        if not should_link:
            return False
        self.calls.append(list(items))
        return True


class ResolveServiceLinkingTests(unittest.TestCase):
    def test_link_all_clips_links_matching_pairs(self):
        service = ResolveService()
        timeline = _FakeTimeline()

        video_a = _FakeTimelineItem("media-a")
        audio_a = _FakeTimelineItem("media-a")
        video_b = _FakeTimelineItem("media-b")
        audio_b = _FakeTimelineItem("media-b")

        service.get_current_timeline = lambda: timeline  # type: ignore[method-assign]
        service._get_timeline_item_records = lambda: [  # type: ignore[method-assign]
            {
                "item": video_a,
                "media_type": "video",
                "track_index": 1,
                "file_path": "/tmp/a.mov",
                "clip_start_frame": 100,
                "clip_end_frame": 180,
                "source_start_frame": 1000,
                "source_end_frame": 1080,
            },
            {
                "item": audio_a,
                "media_type": "audio",
                "track_index": 1,
                "file_path": "/tmp/a.mov",
                "clip_start_frame": 100,
                "clip_end_frame": 180,
                "source_start_frame": 1000,
                "source_end_frame": 1080,
            },
            {
                "item": video_b,
                "media_type": "video",
                "track_index": 1,
                "file_path": "/tmp/b.mov",
                "clip_start_frame": 220,
                "clip_end_frame": 310,
                "source_start_frame": 500,
                "source_end_frame": 590,
            },
            {
                "item": audio_b,
                "media_type": "audio",
                "track_index": 1,
                "file_path": "/tmp/b.mov",
                "clip_start_frame": 220,
                "clip_end_frame": 310,
                "source_start_frame": 500,
                "source_end_frame": 590,
            },
        ]

        result = service.link_all_clips()

        self.assertTrue(result["success"])
        self.assertEqual(result["linked_pairs"], 2)
        self.assertEqual(len(timeline.calls), 2)
        self.assertEqual(timeline.calls[0], [video_a, audio_a])
        self.assertEqual(timeline.calls[1], [video_b, audio_b])

    def test_link_all_clips_ignores_ambiguous_overlap_without_shared_media(self):
        service = ResolveService()
        timeline = _FakeTimeline()

        video_a = _FakeTimelineItem("media-a")
        audio_other = _FakeTimelineItem("media-b")

        service.get_current_timeline = lambda: timeline  # type: ignore[method-assign]
        service._get_timeline_item_records = lambda: [  # type: ignore[method-assign]
            {
                "item": video_a,
                "media_type": "video",
                "track_index": 1,
                "file_path": "/tmp/a.mov",
                "clip_start_frame": 100,
                "clip_end_frame": 180,
                "source_start_frame": 1000,
                "source_end_frame": 1080,
            },
            {
                "item": audio_other,
                "media_type": "audio",
                "track_index": 1,
                "file_path": "/tmp/other.wav",
                "clip_start_frame": 100,
                "clip_end_frame": 180,
                "source_start_frame": 4000,
                "source_end_frame": 4080,
            },
        ]

        result = service.link_all_clips()

        self.assertFalse(result["success"])
        self.assertIn("matching video/audio clip pairs", result["error"])
        self.assertEqual(timeline.calls, [])


if __name__ == "__main__":
    unittest.main()
