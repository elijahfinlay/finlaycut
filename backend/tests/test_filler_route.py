import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from api.routes import filler_words


class _FakeTimeline:
    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return "24"
        return None

    def GetStartFrame(self):
        return 100

    def GetEndFrame(self):
        return 1000

    def GetName(self):
        return "Unit Test Timeline"


class _FakeTimelineItem:
    pass


class FillerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_analyze_uses_cached_master_mix_audio_when_available(self):
        transcript = {
            "words": [
                {"word": "um", "start": 10.0, "end": 10.2, "probability": 0.6},
                {"word": "hello", "start": 10.3, "end": 10.8, "probability": 0.9},
            ]
        }
        detections = [{"word": "um", "start": 10.0, "end": 10.2, "selected": True}]

        with (
            patch.object(filler_words.resolve_service, "_connected", True),
            patch.object(filler_words.resolve_service, "_resolve", object()),
            patch.object(filler_words.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                filler_words,
                "get_last_transcription_audio",
                return_value=("/tmp/cached-transcript.wav", 33.4, 0.0, "sig-123", False, "rendered_master_mix"),
            ),
            patch.object(filler_words.os.path, "exists", return_value=True),
            patch.object(
                filler_words.resolve_service,
                "get_timeline_signature",
                return_value={"timeline_signature": "sig-123"},
            ),
            patch.object(
                filler_words.audio_service,
                "build_waveform_profile",
                return_value={"duration": 33.4},
            ) as build_profile,
            patch.object(
                filler_words.audio_service,
                "analyze_filler_candidates",
                return_value=detections,
            ) as analyze_candidates,
            patch.object(
                filler_words,
                "assemble_current_timeline_audio",
                side_effect=AssertionError("timeline reassembly should not run"),
            ),
            patch.object(
                filler_words.manager,
                "send_progress",
                new=AsyncMock(),
            ),
        ):
            response = self.client.post("/api/filler-words/analyze", json={"transcript": transcript})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["analysis_audio_source"], "cached_master_mix")
        self.assertEqual(payload["detections"][0]["start_frame"], 340)
        build_profile.assert_called_once_with("/tmp/cached-transcript.wav")
        analyze_candidates.assert_called_once()

    def test_analyze_ignores_non_master_mix_cached_audio_and_renders_master_mix_first(self):
        transcript = {
            "words": [
                {"word": "um", "start": 1.2, "end": 1.4, "probability": 0.6},
                {"word": "hello", "start": 1.5, "end": 1.8, "probability": 0.9},
            ]
        }

        with (
            patch.object(filler_words.resolve_service, "_connected", True),
            patch.object(filler_words.resolve_service, "_resolve", object()),
            patch.object(filler_words.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                filler_words.resolve_service,
                "get_timeline_signature",
                return_value={"timeline_signature": "sig-123"},
            ),
            patch.object(
                filler_words,
                "get_last_transcription_audio",
                return_value=("/tmp/cached-transcript.wav", 5.0, 0.0, "sig-123", True, "assembled_best_track"),
            ),
            patch.object(filler_words.os.path, "exists", return_value=True),
            patch.object(
                filler_words,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": "/tmp/rendered.wav",
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 300,
                    "analysis_audio_source": "rendered_master_mix",
                    "analysis_audio_fallback_reason": None,
                },
            ) as prepare_audio,
            patch.object(
                filler_words.audio_service,
                "build_waveform_profile",
                return_value={"duration": 10.0},
            ),
            patch.object(
                filler_words.audio_service,
                "analyze_filler_candidates",
                return_value=[],
            ),
            patch.object(
                filler_words.manager,
                "send_progress",
                new=AsyncMock(),
            ),
        ):
            response = self.client.post("/api/filler-words/analyze", json={"transcript": transcript})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)
        self.assertEqual(response.json()["analysis_audio_source"], "rendered_master_mix")
        prepare_audio.assert_called_once()

    def test_analyze_falls_back_explicitly_when_master_mix_render_is_unavailable(self):
        transcript = {
            "words": [
                {"word": "um", "start": 1.0, "end": 1.2, "probability": 0.6},
                {"word": "hello", "start": 1.3, "end": 1.8, "probability": 0.9},
            ]
        }

        with (
            patch.object(filler_words.resolve_service, "_connected", True),
            patch.object(filler_words.resolve_service, "_resolve", object()),
            patch.object(filler_words.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                filler_words.resolve_service,
                "get_timeline_signature",
                return_value={"timeline_signature": "sig-123"},
            ),
            patch.object(
                filler_words,
                "get_last_transcription_audio",
                return_value=None,
            ),
            patch.object(
                filler_words,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": "/tmp/assembled.wav",
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 300,
                    "analysis_audio_source": "assembled_best_track",
                    "analysis_audio_fallback_reason": "master_mix_render_unavailable",
                },
            ) as prepare_audio,
            patch.object(
                filler_words.audio_service,
                "build_waveform_profile",
                return_value={"duration": 10.0},
            ),
            patch.object(
                filler_words.audio_service,
                "analyze_filler_candidates",
                return_value=[],
            ),
            patch.object(
                filler_words.manager,
                "send_progress",
                new=AsyncMock(),
            ),
        ):
            response = self.client.post("/api/filler-words/analyze", json={"transcript": transcript})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 0)
        self.assertEqual(response.json()["analysis_audio_source"], "assembled_best_track")
        self.assertEqual(response.json()["analysis_audio_fallback_reason"], "master_mix_render_unavailable")
        prepare_audio.assert_called_once()

    def test_analyze_uses_legacy_cached_audio_when_master_mix_default_is_disabled(self):
        transcript = {
            "words": [
                {"word": "um", "start": 1.0, "end": 1.2, "probability": 0.6},
                {"word": "hello", "start": 1.3, "end": 1.8, "probability": 0.9},
            ]
        }

        with (
            patch.object(filler_words.resolve_service, "_connected", True),
            patch.object(filler_words.resolve_service, "_resolve", object()),
            patch.object(filler_words.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(filler_words.settings, "enable_master_mix_analysis_default", False),
            patch.object(
                filler_words.resolve_service,
                "get_timeline_signature",
                return_value={"timeline_signature": "sig-123"},
            ),
            patch.object(
                filler_words,
                "get_last_transcription_audio",
                return_value=("/tmp/cached-transcript.wav", 5.0, 0.0, "sig-123", False, "assembled_best_track"),
            ),
            patch.object(filler_words.os.path, "exists", return_value=True),
            patch.object(
                filler_words.audio_service,
                "build_waveform_profile",
                return_value={"duration": 10.0},
            ),
            patch.object(
                filler_words.audio_service,
                "analyze_filler_candidates",
                return_value=[],
            ),
            patch.object(
                filler_words.manager,
                "send_progress",
                new=AsyncMock(),
            ),
        ):
            response = self.client.post("/api/filler-words/analyze", json={"transcript": transcript})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["analysis_audio_source"], "assembled_best_track")
        self.assertEqual(response.json()["analysis_audio_fallback_reason"], "master_mix_disabled")

    def test_preview_markers_refreshes_only_selected_filler_detections(self):
        detections = [
            {
                "word": "um",
                "start": 1.0,
                "end": 1.2,
                "context": "um hello there",
                "selected": True,
            },
            {
                "word": "uh",
                "start": 2.0,
                "end": 2.1,
                "context": "uh second",
                "selected": False,
            },
            {
                "word": "[silence]",
                "kind": "silence",
                "start": 3.0,
                "end": 3.5,
                "context": "ignored silence",
                "selected": True,
            },
        ]

        with (
            patch.object(filler_words.resolve_service, "_connected", True),
            patch.object(filler_words.resolve_service, "_resolve", object()),
            patch.object(filler_words.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                filler_words.resolve_service,
                "_get_timeline_item_records",
                return_value=[
                    {
                        "item": _FakeTimelineItem(),
                        "media_type": "video",
                        "track_index": 1,
                        "clip_start_frame": 120,
                        "clip_end_frame": 160,
                    }
                ],
            ),
            patch.object(
                filler_words.resolve_service,
                "get_timeline_item_name",
                return_value="Clip A",
            ),
            patch.object(
                filler_words.resolve_service,
                "get_timeline_item_unique_id",
                return_value="item-1",
            ),
            patch.object(
                filler_words.resolve_service,
                "refresh_managed_markers",
                return_value={
                    "success": True,
                    "deleted_count": 4,
                    "applied_count": 4,
                    "timeline_applied_count": 2,
                    "timeline_item_applied_count": 2,
                    "failed_frames": [],
                    "timeline_item_failed_frames": [],
                },
            ) as refresh_markers,
        ):
            response = self.client.post("/api/filler-words/preview-markers", json=detections)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["cleared_count"], 4)
        self.assertEqual(payload["timeline_marker_count"], 2)
        self.assertEqual(payload["clip_marker_count"], 2)

        refresh_markers.assert_called_once()
        self.assertEqual(refresh_markers.call_args.args[0], "finlaycut:filler-preview")
        timeline_markers = refresh_markers.call_args.kwargs["timeline_markers"]
        clip_markers = refresh_markers.call_args.kwargs["timeline_item_markers"]
        self.assertEqual(len(timeline_markers), 2)
        self.assertEqual(timeline_markers[0]["frame_id"], 124)
        self.assertEqual(timeline_markers[0]["duration"], 1)
        self.assertEqual(timeline_markers[0]["name"], "Filler IN - um")
        self.assertEqual(timeline_markers[1]["frame_id"], 128)
        self.assertEqual(timeline_markers[1]["name"], "Filler OUT - um")
        self.assertIn('"word":"um"', timeline_markers[0]["custom_data"])
        self.assertIn('"marker_side":"in"', timeline_markers[0]["custom_data"])
        self.assertIn('Review before cutting', timeline_markers[0]["note"])
        self.assertEqual(len(clip_markers), 2)
        self.assertEqual(clip_markers[0]["frame_id"], 4)
        self.assertEqual(clip_markers[0]["duration"], 1)
        self.assertEqual(clip_markers[0]["name"], "Filler IN - um")
        self.assertEqual(clip_markers[1]["frame_id"], 8)
        self.assertEqual(clip_markers[1]["name"], "Filler OUT - um")
        self.assertIsNotNone(clip_markers[0]["item"])
        self.assertIn('"kind":"filler_clip_range"', clip_markers[0]["custom_data"])

    def test_preview_markers_requires_connection(self):
        with (
            patch.object(filler_words.resolve_service, "_connected", False),
            patch.object(filler_words.resolve_service, "_resolve", None),
        ):
            response = self.client.post("/api/filler-words/preview-markers", json=[])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"], "Not connected to DaVinci Resolve")

    def test_preview_markers_clears_existing_preview_when_no_filler_detections_remain(self):
        with (
            patch.object(filler_words.resolve_service, "_connected", True),
            patch.object(filler_words.resolve_service, "_resolve", object()),
            patch.object(filler_words.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                filler_words.resolve_service,
                "refresh_managed_markers",
                return_value={"success": True, "deleted_count": 3},
            ) as clear_markers,
        ):
            response = self.client.post("/api/filler-words/preview-markers", json=[])

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["cleared_count"], 3)
        clear_markers.assert_called_once_with("finlaycut:filler-preview")


if __name__ == "__main__":
    unittest.main()
