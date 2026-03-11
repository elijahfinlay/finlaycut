import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from api.routes import silence


class _FakeTimeline:
    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return "24"
        return None

    def GetStartFrame(self):
        return 100


class SilenceRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_analyze_silence_prefers_master_mix_analysis(self):
        with (
            patch.object(silence.resolve_service, "_connected", True),
            patch.object(silence.resolve_service, "_resolve", object()),
            patch.object(silence.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(silence.resolve_service, "get_timeline_clips", return_value=[object()]),
            patch.object(
                silence,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": "/tmp/rendered.wav",
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 220,
                    "analysis_audio_source": "rendered_master_mix",
                    "analysis_audio_fallback_reason": None,
                },
            ),
            patch.object(silence.audio_service, "build_waveform_profile", return_value={"duration": 5.0}),
            patch.object(
                silence.audio_service,
                "analyze_silence_candidates",
                return_value=[
                    {
                        "start": 0.8,
                        "end": 1.4,
                        "duration": 0.6,
                        "kind": "silence",
                        "boundary_source": "waveform",
                        "refine_confidence": 0.77,
                        "refine_reason": "validated_dialogue_valley",
                        "used_fallback_seed": False,
                    }
                ],
            ) as analyze_candidates,
            patch.object(silence.manager, "send_progress", new=AsyncMock()),
        ):
            response = self.client.post("/api/silence/analyze", json={
                "threshold_db": -40,
                "min_duration_ms": 300,
                "min_duration_frames": 14,
                "padding_ms": 100,
            })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["analysis_audio_source"], "rendered_master_mix")
        self.assertIsNone(payload["analysis_audio_fallback_reason"])
        self.assertEqual(payload["segment_count"], 1)
        self.assertEqual(payload["segments"][0]["refine_reason"], "validated_dialogue_valley")
        self.assertTrue(analyze_candidates.call_args.kwargs["require_dialogue_valley"])

    def test_analyze_silence_falls_back_to_per_clip_when_master_mix_is_unavailable(self):
        with (
            patch.object(silence.resolve_service, "_connected", True),
            patch.object(silence.resolve_service, "_resolve", object()),
            patch.object(silence.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(silence.resolve_service, "get_timeline_clips", return_value=[object()]),
            patch.object(
                silence,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": None,
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 220,
                    "analysis_audio_source": None,
                    "analysis_audio_fallback_reason": "master_mix_render_unavailable",
                },
            ),
            patch.object(
                silence,
                "_analyze_silence_per_clip_fallback",
                new=AsyncMock(return_value=[]),
            ) as per_clip_fallback,
            patch.object(silence.manager, "send_progress", new=AsyncMock()),
        ):
            response = self.client.post("/api/silence/analyze", json={
                "threshold_db": -40,
                "min_duration_ms": 300,
                "min_duration_frames": 14,
                "padding_ms": 100,
            })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["analysis_audio_source"], "per_clip_best_track")
        self.assertEqual(payload["analysis_audio_fallback_reason"], "master_mix_render_unavailable")
        per_clip_fallback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
