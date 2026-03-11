import unittest

from services.resolve_service import ResolveService


class _FakeTimeline:
    def __init__(self):
        self.markers = {
            100: {
                "color": "Yellow",
                "name": "Old preview",
                "note": "[finlaycut:filler-preview] stale",
                "duration": 4,
                "customData": "finlaycut:filler-preview:old",
            },
            140: {
                "color": "Blue",
                "name": "Manual marker",
                "note": "Leave me alone",
                "duration": 1,
                "customData": "",
            },
            180: {
                "color": "Yellow",
                "name": "Old preview fallback",
                "note": "[finlaycut:filler-preview] note-only",
                "duration": 3,
                "customData": "",
            },
        }
        self.deleted_frames: list[int] = []
        self.add_calls: list[tuple] = []

    def GetMarkers(self):
        return dict(self.markers)

    def DeleteMarkerAtFrame(self, frame_id):
        frame_id = int(frame_id)
        self.deleted_frames.append(frame_id)
        return self.markers.pop(frame_id, None) is not None

    def AddMarker(self, *args):
        self.add_calls.append(args)
        if len(args) == 6:
            frame_id, color, name, note, duration, custom_data = args
        elif len(args) == 5:
            frame_id, color, name, note, duration = args
            custom_data = ""
        else:
            raise TypeError("Unexpected AddMarker arity")

        self.markers[int(frame_id)] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": duration,
            "customData": custom_data,
        }
        return True


class _FakeTimelineWithoutCustomData(_FakeTimeline):
    def __init__(self):
        super().__init__()
        self.rejected_custom_data = 0

    def AddMarker(self, *args):
        if len(args) == 6:
            self.rejected_custom_data += 1
            raise TypeError("custom data unsupported")
        return super().AddMarker(*args)


class _FakeTimelineItem(_FakeTimeline):
    pass


class ResolveServiceMarkerTests(unittest.TestCase):
    def test_refresh_managed_timeline_markers_deletes_only_matching_namespace(self):
        service = ResolveService()
        timeline = _FakeTimeline()
        service.get_current_timeline = lambda: timeline  # type: ignore[method-assign]

        result = service.refresh_managed_timeline_markers(
            "finlaycut:filler-preview",
            [
                {
                    "frame_id": 220,
                    "color": "Yellow",
                    "name": "Filler: um",
                    "note": '"um" | context',
                    "duration": 5,
                    "custom_data": '{"feature":"filler_preview"}',
                }
            ],
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["deleted_count"], 2)
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(timeline.deleted_frames, [100, 180])
        self.assertIn(140, timeline.markers)
        self.assertIn(220, timeline.markers)
        self.assertEqual(timeline.markers[220]["note"], '"um" | context')
        self.assertTrue(timeline.markers[220]["customData"].startswith("finlaycut:filler-preview:"))

    def test_refresh_managed_markers_refreshes_timeline_and_item_markers(self):
        service = ResolveService()
        timeline = _FakeTimeline()
        item = _FakeTimelineItem()
        service.get_current_timeline = lambda: timeline  # type: ignore[method-assign]
        service._get_timeline_item_records = lambda media_types=("video", "audio"): [  # type: ignore[method-assign]
            {
                "item": item,
                "media_type": "video",
                "track_index": 1,
                "clip_start_frame": 100,
                "clip_end_frame": 220,
            }
        ]

        result = service.refresh_managed_markers(
            "finlaycut:filler-preview",
            timeline_markers=[
                {
                    "frame_id": 220,
                    "color": "Yellow",
                    "name": "Filler - um",
                    "note": "timeline preview",
                    "duration": 5,
                    "custom_data": '{"feature":"filler_preview"}',
                }
            ],
            timeline_item_markers=[
                {
                    "item": item,
                    "frame_id": 24,
                    "color": "Yellow",
                    "name": "Filler - um",
                    "note": "clip preview",
                    "duration": 5,
                    "custom_data": '{"feature":"filler_preview","kind":"filler_clip_range"}',
                }
            ],
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["timeline_deleted_count"], 2)
        self.assertEqual(result["timeline_item_deleted_count"], 2)
        self.assertEqual(result["timeline_applied_count"], 1)
        self.assertEqual(result["timeline_item_applied_count"], 1)
        self.assertIn(220, timeline.markers)
        self.assertIn(24, item.markers)
        self.assertEqual(timeline.deleted_frames, [100, 180])
        self.assertEqual(item.deleted_frames, [100, 180])
        self.assertIn(140, timeline.markers)
        self.assertIn(140, item.markers)

    def test_add_marker_falls_back_when_custom_data_is_not_supported(self):
        service = ResolveService()
        timeline = _FakeTimelineWithoutCustomData()
        service.get_current_timeline = lambda: timeline  # type: ignore[method-assign]

        success = service.add_marker(
            frame_id=300,
            color="Yellow",
            name="Filler: uh",
            note="preview",
            duration=6,
            custom_data="finlaycut:filler-preview:test",
        )

        self.assertTrue(success)
        self.assertEqual(timeline.rejected_custom_data, 1)
        self.assertEqual(len(timeline.add_calls), 1)
        self.assertEqual(len(timeline.add_calls[0]), 5)
        self.assertEqual(timeline.markers[300]["customData"], "")


if __name__ == "__main__":
    unittest.main()
