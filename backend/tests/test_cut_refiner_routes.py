import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from api.routes import filler_words, repetition, silence, transcript_editor
from services.cut_refiner_service import cut_refiner_service


class _FakeTimeline:
    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return "24"
        return None

    def GetStartFrame(self):
        return 100

    def GetEndFrame(self):
        return 340

    def GetName(self):
        return "Tool Testing"


class _FakeBackup:
    def GetName(self):
        return "Tool Testing Backup"


def _refinement_result():
    return {
        "cut_frame_ranges": [(120, 132)],
        "kept_segments": [
            {"start": 0.0, "end": 0.8333, "start_frame": 100, "end_frame": 120},
            {"start": 1.3333, "end": 10.0, "start_frame": 132, "end_frame": 340},
        ],
        "applied_cut_segments": [
            {"start": 0.8333, "end": 1.3333, "start_frame": 120, "end_frame": 132, "kind": "speech", "boundary_source": "waveform"}
        ],
    }


class SharedCutRefinerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_transcript_apply_uses_shared_cut_refiner(self):
        request_body = {
            "cut_segments": [
                {
                    "start": 0.75,
                    "end": 1.4,
                    "start_frame": 118,
                    "end_frame": 134,
                    "kind": "speech",
                    "prev_word_end": 0.72,
                    "next_word_start": 1.42,
                }
            ],
            "muted_segments": [],
            "transcript_session": {
                "session_id": "sess-1",
                "timeline_name": "Tool Testing",
                "timeline_signature": "sig-123",
                "last_synced_at": "2026-03-07T12:00:00Z",
                "sync_state": "fresh",
                "origin": "transcribe",
            },
        }

        with (
            patch.object(transcript_editor.resolve_service, "_connected", True),
            patch.object(transcript_editor.resolve_service, "_resolve", object()),
            patch.object(transcript_editor.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(transcript_editor.resolve_service, "get_timeline_signature", return_value={"timeline_signature": "sig-123"}),
            patch.object(transcript_editor.resolve_service, "duplicate_timeline", return_value=_FakeBackup()),
            patch.object(
                transcript_editor.resolve_service,
                "rebuild_timeline_with_audio_mutes",
                return_value={"success": True, "timeline_name": "Tool Testing"},
            ) as rebuild,
            patch.object(
                transcript_editor.cut_refiner_service,
                "refine_cut_candidates",
                return_value=_refinement_result(),
            ) as refine,
            patch.object(transcript_editor.manager, "send_progress", new=AsyncMock()),
            patch.object(transcript_editor.manager, "send_complete", new=AsyncMock()),
            patch("api.routes.undo._record", return_value={"id": "undo-1"}),
        ):
            response = self.client.post("/api/transcript-editor/apply", json=request_body)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["applied_cut_segments"][0]["start_frame"], 120)
        self.assertEqual(refine.call_args.kwargs["name_prefix"], "finlaycut_transcript_apply")
        refine_candidates = refine.call_args.args[0]
        self.assertEqual(refine_candidates[0]["kind"], "speech")
        rebuild.assert_called_once()
        self.assertEqual(rebuild.call_args.kwargs["kept_segments"], _refinement_result()["kept_segments"])

    def test_filler_apply_uses_shared_cut_refiner(self):
        detections = [
            {
                "word": "um",
                "start": 0.8,
                "end": 1.2,
                "start_frame": 119,
                "end_frame": 131,
                "kind": "audio_hesitation",
                "selected": True,
            }
        ]

        with (
            patch.object(filler_words.resolve_service, "_connected", True),
            patch.object(filler_words.resolve_service, "_resolve", object()),
            patch.object(filler_words.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(filler_words.resolve_service, "duplicate_timeline", return_value=_FakeBackup()),
            patch.object(
                filler_words.resolve_service,
                "rebuild_timeline_keeping",
                return_value={"success": True, "timeline_name": "Tool Testing"},
            ) as rebuild,
            patch.object(
                filler_words.cut_refiner_service,
                "refine_cut_candidates",
                return_value=_refinement_result(),
            ) as refine,
            patch.object(filler_words.manager, "send_progress", new=AsyncMock()),
            patch.object(filler_words.manager, "send_complete", new=AsyncMock()),
            patch("api.routes.undo._record", return_value={"id": "undo-1"}),
        ):
            response = self.client.post("/api/filler-words/apply", json=detections)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["applied_cut_segments"][0]["start_frame"], 120)
        self.assertEqual(refine.call_args.kwargs["name_prefix"], "finlaycut_filler_apply")
        rebuild.assert_called_once()

    def test_repetition_apply_uses_shared_cut_refiner(self):
        groups = [
            {
                "best_index": 0,
                "takes": [
                    {"start": 0.0, "end": 0.7, "start_frame": 100, "end_frame": 117},
                    {"start": 0.8, "end": 1.4, "start_frame": 119, "end_frame": 134},
                ],
            }
        ]

        with (
            patch.object(repetition.resolve_service, "_connected", True),
            patch.object(repetition.resolve_service, "_resolve", object()),
            patch.object(repetition.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(repetition.resolve_service, "duplicate_timeline", return_value=_FakeBackup()),
            patch.object(
                repetition.resolve_service,
                "rebuild_timeline_keeping",
                return_value={"success": True, "timeline_name": "Tool Testing"},
            ) as rebuild,
            patch.object(
                repetition.cut_refiner_service,
                "refine_cut_candidates",
                return_value=_refinement_result(),
            ) as refine,
            patch("api.routes.undo._record", return_value={"id": "undo-1"}),
        ):
            response = self.client.post("/api/repetition/apply", json=groups)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["applied_cut_segments"][0]["start_frame"], 120)
        self.assertEqual(refine.call_args.kwargs["name_prefix"], "finlaycut_repetition_apply")
        rebuild.assert_called_once()

    def test_silence_apply_uses_shared_cut_refiner(self):
        request_body = {
            "segments": [
                {
                    "start": 0.8,
                    "end": 1.6,
                    "duration": 0.8,
                    "kind": "silence",
                    "start_frame": 119,
                    "end_frame": 138,
                    "selected": True,
                }
            ],
            "padding_ms": 120,
        }

        silence_result = _refinement_result()
        silence_result["applied_cut_segments"][0]["kind"] = "silence"

        with (
            patch.object(silence.resolve_service, "_connected", True),
            patch.object(silence.resolve_service, "_resolve", object()),
            patch.object(silence.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(silence.resolve_service, "duplicate_timeline", return_value=_FakeBackup()),
            patch.object(
                silence.resolve_service,
                "rebuild_timeline_keeping",
                return_value={"success": True, "timeline_name": "Tool Testing"},
            ) as rebuild,
            patch.object(
                silence.cut_refiner_service,
                "refine_cut_candidates",
                return_value=silence_result,
            ) as refine,
            patch.object(silence.manager, "send_progress", new=AsyncMock()),
            patch.object(silence.manager, "send_complete", new=AsyncMock()),
            patch("api.routes.undo._record", return_value={"id": "undo-1"}),
        ):
            response = self.client.post("/api/silence/apply", json=request_body)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["applied_cut_segments"][0]["kind"], "silence")
        self.assertEqual(refine.call_args.kwargs["default_silence_padding_ms"], 120)
        rebuild.assert_called_once()

    def test_shared_cut_refiner_uses_spoken_solver_when_flag_enabled(self):
        candidates = [
            {
                "kind": "speech",
                "start": 0.8,
                "end": 1.2,
                "start_frame": 119,
                "end_frame": 131,
            }
        ]
        with (
            patch("services.cut_refiner_service.get_precision_timeline_audio", return_value=("/tmp/fake.wav", 24.0, 100, 300)),
            patch("services.cut_refiner_service.settings.enable_spoken_waveform_refiner", True),
            patch("services.cut_refiner_service.audio_service.build_waveform_profile", return_value={"duration": 8.0}),
            patch("services.cut_refiner_service.audio_service.refine_spoken_segments_to_waveform", return_value=[
                {
                    "kind": "speech",
                    "start": 0.75,
                    "end": 1.22,
                    "boundary_source": "waveform",
                    "refine_confidence": 0.81,
                    "refine_reason": "continuation_component",
                    "used_fallback_seed": False,
                }
            ]) as spoken_refine,
            patch("services.cut_refiner_service.audio_service.refine_segments_to_low_energy", side_effect=AssertionError("legacy low-energy path should not run")),
        ):
            result = cut_refiner_service.refine_cut_candidates(candidates)

        self.assertEqual(len(result["applied_cut_segments"]), 1)
        self.assertEqual(result["applied_cut_segments"][0]["boundary_source"], "waveform")
        self.assertEqual(result["applied_cut_segments"][0]["refine_reason"], "continuation_component")
        spoken_refine.assert_called_once()

    def test_shared_cut_refiner_keeps_legacy_generic_path_when_flag_disabled(self):
        candidates = [
            {
                "kind": "retake",
                "start": 0.8,
                "end": 1.2,
                "start_frame": 119,
                "end_frame": 131,
            }
        ]
        with (
            patch("services.cut_refiner_service.get_precision_timeline_audio", return_value=("/tmp/fake.wav", 24.0, 100, 300)),
            patch("services.cut_refiner_service.settings.enable_spoken_waveform_refiner", False),
            patch("services.cut_refiner_service.audio_service.build_waveform_profile", return_value={"duration": 8.0}),
            patch("services.cut_refiner_service.audio_service.refine_segments_to_low_energy", return_value=[
                {
                    "kind": "retake",
                    "start": 0.82,
                    "end": 1.16,
                    "boundary_source": "waveform",
                    "refine_confidence": None,
                    "refine_reason": "local_low_energy_trim",
                    "used_fallback_seed": False,
                }
            ]) as low_energy_refine,
            patch("services.cut_refiner_service.audio_service.refine_spoken_segments_to_waveform", side_effect=AssertionError("spoken solver should be disabled")),
        ):
            result = cut_refiner_service.refine_cut_candidates(candidates)

        self.assertEqual(len(result["applied_cut_segments"]), 1)
        self.assertEqual(result["applied_cut_segments"][0]["refine_reason"], "local_low_energy_trim")
        low_energy_refine.assert_called_once()


if __name__ == "__main__":
    unittest.main()
