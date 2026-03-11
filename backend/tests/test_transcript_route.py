import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from api.routes import transcript_editor


class _FakeTimeline:
    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return "24"
        return None

    def GetStartFrame(self):
        return 100

    def GetEndFrame(self):
        return 220


class TranscriptRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_transcribe_uses_render_fallback_and_returns_words(self):
        fake_transcript = {
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.5, "probability": 0.98},
            ],
            "language": "en",
        }

        with (
            patch.object(transcript_editor.resolve_service, "_connected", True),
            patch.object(transcript_editor.resolve_service, "_resolve", object()),
            patch.object(transcript_editor.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_audio_clip_contexts",
                return_value={
                    "contexts": [],
                    "has_any_items": True,
                    "attempts": ["compound-clip"],
                    "selected_media_type": "audio",
                    "selected_track_index": 1,
                },
            ),
            patch.object(
                transcript_editor.resolve_service,
                "render_current_timeline_audio",
                return_value="/tmp/fake-transcript-audio.wav",
            ) as render_audio,
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_signature",
                return_value={
                    "timeline_name": "Unit Test Timeline",
                    "timeline_signature": "sig-123",
                },
            ),
            patch.object(
                transcript_editor.audio_service,
                "transcribe_with_progress",
                new=AsyncMock(return_value=fake_transcript),
            ),
            patch.object(
                transcript_editor.manager,
                "send_complete",
                new=AsyncMock(),
            ) as send_complete,
        ):
            response = self.client.post("/api/transcript-editor/transcribe", json={
                "language": "en",
                "detect_speakers": False,
            })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["word_count"], 1)
        self.assertEqual(payload["words"][0]["word"], "hello")
        self.assertEqual(payload["words"][0]["start_frame"], 100)
        self.assertEqual(payload["transcript_session"]["timeline_signature"], "sig-123")
        self.assertEqual(payload["analysis_audio_source"], "rendered_master_mix")
        self.assertIsNone(payload["analysis_audio_fallback_reason"])
        render_audio.assert_called_once_with(
            name_prefix="finlaycut_transcript",
            mark_in_frame=None,
            mark_out_frame=None,
            fallback_restore_page="edit",
        )
        send_complete.assert_awaited_once()

    def test_transcribe_does_not_run_diarization_by_default(self):
        fake_transcript = {
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.5, "probability": 0.98},
            ],
            "language": "en",
        }

        with (
            patch.object(transcript_editor.resolve_service, "_connected", True),
            patch.object(transcript_editor.resolve_service, "_resolve", object()),
            patch.object(transcript_editor.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_audio_clip_contexts",
                return_value={
                    "contexts": [],
                    "has_any_items": True,
                    "attempts": ["compound-clip"],
                    "selected_media_type": "audio",
                    "selected_track_index": 1,
                },
            ),
            patch.object(
                transcript_editor.resolve_service,
                "render_current_timeline_audio",
                return_value="/tmp/fake-transcript-audio.wav",
            ),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_signature",
                return_value={
                    "timeline_name": "Unit Test Timeline",
                    "timeline_signature": "sig-123",
                },
            ),
            patch.object(
                transcript_editor.audio_service,
                "transcribe_with_progress",
                new=AsyncMock(return_value=fake_transcript),
            ),
            patch.object(
                transcript_editor.diarization_service,
                "diarize_with_progress",
                new=AsyncMock(side_effect=AssertionError("diarization should not run")),
            ) as diarize,
        ):
            response = self.client.post("/api/transcript-editor/transcribe", json={
                "language": "en",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["word_count"], 1)
        diarize.assert_not_awaited()

    def test_transcribe_returns_initial_filler_detections_with_timeline_offsets(self):
        fake_transcript = {
            "words": [
                {"word": "hello", "start": 5.0, "end": 5.5, "probability": 0.98},
                {"word": "world", "start": 5.8, "end": 6.1, "probability": 0.98},
            ],
            "language": "en",
        }
        fake_context = {
            "file_path": "/tmp/source.mov",
            "clip_start_frame": 220,
            "clip_end_frame": 460,
            "clip_timeline_offset": 5.0,
            "clip_duration_seconds": 10.0,
            "source_start_seconds": 12.0,
            "source_end_seconds": 22.0,
            "timeline_start_frame": 220,
            "timeline_end_frame": 460,
            "source_start_frame": 1000,
            "source_end_frame": 1240,
            "clip_item_id": "clip-1",
        }
        filler_detection = {
            "word": "[filler]",
            "kind": "audio_hesitation",
            "start": 5.2,
            "end": 5.3,
            "selected": True,
        }

        with (
            patch.object(transcript_editor.resolve_service, "_connected", True),
            patch.object(transcript_editor.resolve_service, "_resolve", object()),
            patch.object(transcript_editor.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_audio_clip_contexts",
                return_value={
                    "contexts": [fake_context],
                    "has_any_items": True,
                    "attempts": ["source-clip"],
                    "selected_media_type": "audio",
                    "selected_track_index": 1,
                },
            ),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_signature",
                return_value={
                    "timeline_name": "Unit Test Timeline",
                    "timeline_signature": "sig-123",
                },
            ),
            patch.object(
                transcript_editor,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": "/tmp/rendered-master-mix.wav",
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 220,
                    "analysis_audio_source": "rendered_master_mix",
                    "analysis_audio_fallback_reason": None,
                },
            ),
            patch.object(
                transcript_editor.audio_service,
                "transcribe_with_progress",
                new=AsyncMock(return_value=fake_transcript),
            ),
            patch.object(
                transcript_editor.audio_service,
                "analyze_filler_candidates",
                return_value=[filler_detection],
            ) as analyze_candidates,
        ):
            response = self.client.post("/api/transcript-editor/transcribe", json={
                "language": "en",
                "filler_words": ["um", "uh"],
            })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["word_count"], 2)
        self.assertEqual(len(payload["filler_detections"]), 1)
        detection = payload["filler_detections"][0]
        self.assertEqual(detection["kind"], "audio_hesitation")
        self.assertAlmostEqual(detection["start"], 5.2, places=3)
        self.assertAlmostEqual(detection["end"], 5.3, places=3)
        self.assertEqual(detection["start_frame"], 225)
        self.assertEqual(detection["end_frame"], 227)
        self.assertEqual(detection["source_start_frame"], 1005)
        self.assertEqual(detection["source_end_frame"], 1007)
        self.assertEqual(detection["clip_item_id"], "clip-1")
        self.assertEqual(detection["file_path"], "/tmp/source.mov")
        self.assertEqual(payload["analysis_audio_source"], "rendered_master_mix")
        self.assertEqual(analyze_candidates.call_args.kwargs["filler_words"], ["um", "uh"])

    def test_transcribe_keeps_session_bound_to_starting_timeline(self):
        fake_transcript = {
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.5, "probability": 0.98},
            ],
            "language": "en",
        }

        with (
            patch.object(transcript_editor.resolve_service, "_connected", True),
            patch.object(transcript_editor.resolve_service, "_resolve", object()),
            patch.object(transcript_editor.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_audio_clip_contexts",
                return_value={
                    "contexts": [],
                    "has_any_items": True,
                    "attempts": ["compound-clip"],
                    "selected_media_type": "audio",
                    "selected_track_index": 1,
                },
            ),
            patch.object(
                transcript_editor.resolve_service,
                "render_current_timeline_audio",
                return_value="/tmp/fake-transcript-audio.wav",
            ),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_signature",
                side_effect=[
                    {
                        "timeline_name": "Timeline A",
                        "timeline_signature": "sig-a",
                    },
                    {
                        "timeline_name": "Timeline B",
                        "timeline_signature": "sig-b",
                    },
                ],
            ) as get_signature,
            patch.object(
                transcript_editor.audio_service,
                "transcribe_with_progress",
                new=AsyncMock(return_value=fake_transcript),
            ),
        ):
            response = self.client.post("/api/transcript-editor/transcribe", json={
                "language": "en",
            })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["transcript_session"]["timeline_name"], "Timeline A")
        self.assertEqual(payload["transcript_session"]["timeline_signature"], "sig-a")
        self.assertEqual(get_signature.call_count, 1)

    def test_transcribe_returns_error_when_no_words_are_produced(self):
        fake_transcript = {
            "words": [],
            "language": "en",
            "text": "hello world",
        }

        with (
            patch.object(transcript_editor.resolve_service, "_connected", True),
            patch.object(transcript_editor.resolve_service, "_resolve", object()),
            patch.object(transcript_editor.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_audio_clip_contexts",
                return_value={
                    "contexts": [],
                    "has_any_items": True,
                    "attempts": ["compound-clip"],
                    "selected_media_type": "audio",
                    "selected_track_index": 1,
                },
            ),
            patch.object(
                transcript_editor.resolve_service,
                "render_current_timeline_audio",
                return_value="/tmp/fake-transcript-audio.wav",
            ),
            patch.object(
                transcript_editor.resolve_service,
                "get_timeline_signature",
                return_value={
                    "timeline_name": "Unit Test Timeline",
                    "timeline_signature": "sig-123",
                },
            ),
            patch.object(
                transcript_editor.audio_service,
                "transcribe_with_progress",
                new=AsyncMock(return_value=fake_transcript),
            ),
        ):
            response = self.client.post("/api/transcript-editor/transcribe", json={
                "language": "en",
            })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], "EMPTY_TRANSCRIPT")
        self.assertEqual(payload["word_count"], 0)
        self.assertIn("without any word timings", payload["error"])


if __name__ == "__main__":
    unittest.main()
