import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app
from api.routes import diarization


class _FakeTimeline:
    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return "24"
        return None

    def GetStartFrame(self):
        return 100

    def GetEndFrame(self):
        return 340


class DiarizationRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_annotate_transcript_prefers_master_mix_analysis(self):
        with (
            patch.object(diarization.resolve_service, "_connected", True),
            patch.object(diarization.resolve_service, "_resolve", object()),
            patch.object(diarization.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                diarization.resolve_service,
                "get_timeline_audio_clip_contexts",
                return_value={
                    "contexts": [],
                    "has_any_items": True,
                    "attempts": ["compound-clip"],
                },
            ),
            patch.object(
                diarization,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": "/tmp/rendered.wav",
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 340,
                    "analysis_audio_source": "rendered_master_mix",
                    "analysis_audio_fallback_reason": None,
                },
            ),
            patch.object(
                diarization.diarization_service,
                "diarize_with_progress",
                new=AsyncMock(return_value=([{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.0}], "pyannote", None)),
            ),
            patch.object(
                diarization.diarization_service,
                "assign_speakers_to_words",
                return_value=[{"word": "hello", "speaker": "SPEAKER_00"}],
            ),
            patch.object(diarization, "compute_speaker_loudness", return_value={}),
            patch.object(diarization, "build_speaker_summaries", return_value=[{"speaker": "SPEAKER_00"}]),
            patch.object(diarization.manager, "send_progress", new=AsyncMock()),
        ):
            response = self.client.post("/api/diarization/annotate-transcript", json={
                "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
            })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["analysis_audio_source"], "rendered_master_mix")
        self.assertIsNone(payload["analysis_audio_fallback_reason"])
        self.assertEqual(payload["speaker_count"], 1)

    def test_analyze_speakers_uses_legacy_clip_audio_when_flag_disabled(self):
        with (
            patch.object(diarization.resolve_service, "_connected", True),
            patch.object(diarization.resolve_service, "_resolve", object()),
            patch.object(diarization.settings, "enable_master_mix_analysis_default", False),
            patch.object(diarization.resolve_service, "get_clip_at_playhead", return_value=object()),
            patch.object(diarization.resolve_service, "get_clip_file_path", return_value="/tmp/source.mov"),
            patch.object(diarization, "extract_audio", return_value="/tmp/source.wav"),
            patch.object(
                diarization.diarization_service,
                "diarize_with_progress",
                new=AsyncMock(return_value=([{"speaker": "SPEAKER_00", "start": 0.0, "end": 1.0}], "pyannote", None)),
            ),
            patch.object(diarization.manager, "send_progress", new=AsyncMock()),
        ):
            response = self.client.post("/api/diarization/analyze")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["analysis_audio_source"], "per_clip_best_track")
        self.assertEqual(payload["analysis_audio_fallback_reason"], "master_mix_disabled")


if __name__ == "__main__":
    unittest.main()
