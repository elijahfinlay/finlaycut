import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app
from api.routes import repetition


class _FakeTimeline:
    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return "24"
        return None

    def GetStartFrame(self):
        return 100

    def GetName(self):
        return "Unit Test Timeline"


class RepetitionRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_detect_repetitions_uses_transcript_fast_path(self):
        response = self.client.post("/api/repetition/detect", json={
            "transcript": {
                "words": [
                    {"word": "we", "start": 0.0, "end": 0.1, "probability": 0.95},
                    {"word": "need", "start": 0.1, "end": 0.2, "probability": 0.95},
                    {"word": "to", "start": 0.2, "end": 0.3, "probability": 0.95},
                    {"word": "ship", "start": 0.3, "end": 0.4, "probability": 0.95},
                    {"word": "we", "start": 1.0, "end": 1.1, "probability": 0.96},
                    {"word": "need", "start": 1.1, "end": 1.2, "probability": 0.96},
                    {"word": "to", "start": 1.2, "end": 1.3, "probability": 0.96},
                    {"word": "ship", "start": 1.3, "end": 1.4, "probability": 0.96},
                    {"word": "now", "start": 1.4, "end": 1.5, "probability": 0.96},
                ]
            }
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["groups"][0]["matched_text"], "we need to ship")
        self.assertEqual(payload["groups"][0]["best_index"], 1)

    def test_detect_repetitions_with_empty_transcript_returns_no_groups(self):
        response = self.client.post("/api/repetition/detect", json={
            "transcript": {"words": []}
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"groups": [], "count": 0})

    def test_build_duplicate_clip_groups_uses_exact_source_span_and_keeps_all_instances(self):
        records = [
            {
                "item": object(),
                "media_pool_item_unique_id": "media-1",
                "file_path": "/tmp/source-a.mov",
                "clip_name": "Intro A",
                "track_index": 1,
                "timeline_item_unique_id": "clip-1",
                "clip_start_frame": 100,
                "clip_end_frame": 124,
                "source_start_frame": 10,
                "source_end_frame": 34,
            },
            {
                "item": object(),
                "media_pool_item_unique_id": "media-1",
                "file_path": "/tmp/source-a.mov",
                "clip_name": "Intro B",
                "track_index": 2,
                "timeline_item_unique_id": "clip-2",
                "clip_start_frame": 220,
                "clip_end_frame": 244,
                "source_start_frame": 10,
                "source_end_frame": 34,
            },
            {
                "item": object(),
                "media_pool_item_unique_id": "media-1",
                "file_path": "/tmp/source-a.mov",
                "clip_name": "Different Trim",
                "track_index": 1,
                "timeline_item_unique_id": "clip-3",
                "clip_start_frame": 340,
                "clip_end_frame": 364,
                "source_start_frame": 12,
                "source_end_frame": 36,
            },
            {
                "item": object(),
                "media_pool_item_unique_id": "",
                "file_path": "",
                "clip_name": "Missing Source",
                "track_index": 1,
                "timeline_item_unique_id": "clip-4",
                "clip_start_frame": 460,
                "clip_end_frame": 484,
                "source_start_frame": 10,
                "source_end_frame": 34,
            },
            {
                "item": object(),
                "media_pool_item_unique_id": "media-2",
                "file_path": "/tmp/source-b.mov",
                "clip_name": "Missing Span",
                "track_index": 1,
                "timeline_item_unique_id": "clip-5",
                "clip_start_frame": 580,
                "clip_end_frame": 604,
                "source_start_frame": None,
                "source_end_frame": None,
            },
        ]

        groups, skipped = repetition._build_duplicate_clip_groups(records, timeline_start_frame=100, fps=24.0)

        self.assertEqual(skipped, 2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["instance_count"], 2)
        self.assertEqual(groups[0]["source_start_frame"], 10)
        self.assertEqual(groups[0]["source_end_frame"], 34)
        self.assertEqual(
            [instance["timeline_item_unique_id"] for instance in groups[0]["instances"]],
            ["clip-1", "clip-2"],
        )

    def test_duplicate_clip_scan_refreshes_video_markers_and_returns_groups(self):
        records = [
            {
                "item": object(),
                "media_pool_item_unique_id": "media-1",
                "file_path": "/tmp/source-a.mov",
                "clip_name": "Intro A",
                "track_index": 1,
                "timeline_item_unique_id": "clip-1",
                "clip_start_frame": 100,
                "clip_end_frame": 124,
                "source_start_frame": 10,
                "source_end_frame": 34,
            },
            {
                "item": object(),
                "media_pool_item_unique_id": "media-1",
                "file_path": "/tmp/source-a.mov",
                "clip_name": "Intro B",
                "track_index": 2,
                "timeline_item_unique_id": "clip-2",
                "clip_start_frame": 220,
                "clip_end_frame": 244,
                "source_start_frame": 10,
                "source_end_frame": 34,
            },
            {
                "item": object(),
                "media_pool_item_unique_id": "media-2",
                "file_path": "/tmp/source-b.mov",
                "clip_name": "Single Use",
                "track_index": 1,
                "timeline_item_unique_id": "clip-3",
                "clip_start_frame": 340,
                "clip_end_frame": 364,
                "source_start_frame": 40,
                "source_end_frame": 64,
            },
        ]

        with (
            patch.object(repetition.resolve_service, "_connected", True),
            patch.object(repetition.resolve_service, "_resolve", object()),
            patch.object(repetition.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(repetition.resolve_service, "_get_timeline_item_records", return_value=records) as get_records,
            patch.object(
                repetition.resolve_service,
                "refresh_managed_markers",
                return_value={
                    "success": True,
                    "deleted_count": 1,
                    "timeline_applied_count": 2,
                    "timeline_item_applied_count": 2,
                    "failed_frames": [],
                    "timeline_item_failed_frames": [],
                },
            ) as refresh_markers,
        ):
            response = self.client.post("/api/repetition/duplicate-clips/scan")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["duplicate_group_count"], 1)
        self.assertEqual(payload["duplicate_instance_count"], 2)
        self.assertEqual(payload["timeline_marker_count"], 2)
        self.assertEqual(payload["clip_marker_count"], 2)
        self.assertEqual(payload["skipped_untrackable_count"], 0)
        self.assertEqual(len(payload["groups"]), 1)
        self.assertEqual(payload["groups"][0]["instance_count"], 2)
        self.assertEqual(payload["groups"][0]["instances"][0]["clip_name"], "Intro A")
        self.assertEqual(payload["groups"][0]["instances"][1]["track_index"], 2)

        get_records.assert_called_once_with(media_types=("video",))
        self.assertEqual(refresh_markers.call_args.args[0], "finlaycut:duplicate-clip-review")
        self.assertEqual(refresh_markers.call_args.kwargs["clear_item_media_types"], ("video",))
        timeline_markers = refresh_markers.call_args.kwargs["timeline_markers"]
        clip_markers = refresh_markers.call_args.kwargs["timeline_item_markers"]
        self.assertEqual(len(timeline_markers), 2)
        self.assertEqual(timeline_markers[0]["duration"], 24)
        self.assertEqual(timeline_markers[0]["name"], "Duplicate Clip - Intro A")
        self.assertEqual(timeline_markers[1]["frame_id"], 220)
        self.assertEqual(len(clip_markers), 2)
        self.assertEqual(clip_markers[0]["frame_id"], 10)
        self.assertEqual(clip_markers[0]["duration"], 24)
        self.assertIn('"marker_target":"timeline"', timeline_markers[0]["custom_data"])
        self.assertIn('"marker_target":"timeline_item"', clip_markers[0]["custom_data"])

    def test_duplicate_clip_scan_clears_markers_when_no_duplicates_remain(self):
        with (
            patch.object(repetition.resolve_service, "_connected", True),
            patch.object(repetition.resolve_service, "_resolve", object()),
            patch.object(repetition.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(repetition.resolve_service, "_get_timeline_item_records", return_value=[]),
            patch.object(
                repetition.resolve_service,
                "refresh_managed_markers",
                return_value={"success": True, "deleted_count": 3},
            ) as refresh_markers,
        ):
            response = self.client.post("/api/repetition/duplicate-clips/scan")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["groups"], [])
        self.assertEqual(payload["cleared_count"], 3)
        self.assertEqual(payload["timeline_marker_count"], 0)
        self.assertEqual(payload["clip_marker_count"], 0)
        refresh_markers.assert_called_once_with(
            "finlaycut:duplicate-clip-review",
            clear_item_media_types=("video",),
        )

    def test_duplicate_clip_scan_returns_warning_when_some_markers_fail(self):
        records = [
            {
                "item": object(),
                "media_pool_item_unique_id": "media-1",
                "file_path": "/tmp/source-a.mov",
                "clip_name": "Intro A",
                "track_index": 1,
                "timeline_item_unique_id": "clip-1",
                "clip_start_frame": 100,
                "clip_end_frame": 124,
                "source_start_frame": 10,
                "source_end_frame": 34,
            },
            {
                "item": object(),
                "media_pool_item_unique_id": "media-1",
                "file_path": "/tmp/source-a.mov",
                "clip_name": "Intro B",
                "track_index": 2,
                "timeline_item_unique_id": "clip-2",
                "clip_start_frame": 220,
                "clip_end_frame": 244,
                "source_start_frame": 10,
                "source_end_frame": 34,
            },
        ]

        with (
            patch.object(repetition.resolve_service, "_connected", True),
            patch.object(repetition.resolve_service, "_resolve", object()),
            patch.object(repetition.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(repetition.resolve_service, "_get_timeline_item_records", return_value=records),
            patch.object(
                repetition.resolve_service,
                "refresh_managed_markers",
                return_value={
                    "success": False,
                    "deleted_count": 0,
                    "timeline_applied_count": 1,
                    "timeline_item_applied_count": 2,
                    "failed_frames": [220],
                    "timeline_item_failed_frames": [],
                },
            ),
        ):
            response = self.client.post("/api/repetition/duplicate-clips/scan")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertIn("warning", payload)
        self.assertIn("Some markers could not be added", payload["warning"])

    def test_duplicate_clip_scan_requires_connection(self):
        with (
            patch.object(repetition.resolve_service, "_connected", False),
            patch.object(repetition.resolve_service, "_resolve", None),
        ):
            response = self.client.post("/api/repetition/duplicate-clips/scan")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"], "Not connected to DaVinci Resolve")

    def test_duplicate_clip_scan_requires_timeline(self):
        with (
            patch.object(repetition.resolve_service, "_connected", True),
            patch.object(repetition.resolve_service, "_resolve", object()),
            patch.object(repetition.resolve_service, "get_current_timeline", return_value=None),
        ):
            response = self.client.post("/api/repetition/duplicate-clips/scan")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"], "No active timeline")


if __name__ == "__main__":
    unittest.main()
