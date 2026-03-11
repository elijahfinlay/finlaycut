import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
from fastapi.testclient import TestClient

from main import app
from api.routes import waveform


class _FakeTimeline:
    def GetSetting(self, key):
        if key == "timelineFrameRate":
            return "24"
        return None

    def GetStartFrame(self):
        return 100

    def GetEndFrame(self):
        return 340


class WaveformRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_waveform_ignores_cached_audio_when_it_does_not_cover_full_timeline(self):
        fake_samples = np.ones(22050, dtype=np.float32)

        with (
            patch.object(waveform.resolve_service, "_connected", True),
            patch.object(waveform.resolve_service, "_resolve", object()),
            patch.object(waveform.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                waveform,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": "/tmp/rendered.wav",
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 340,
                    "analysis_audio_source": "rendered_master_mix",
                    "analysis_audio_fallback_reason": None,
                },
            ) as prepare_audio,
            patch("librosa.load", return_value=(fake_samples, 22050)),
            patch.object(
                waveform.manager,
                "send_progress",
                new=AsyncMock(),
            ),
        ):
            response = self.client.post("/api/waveform/analyze?samples=100")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sample_count"], 100)
        self.assertEqual(payload["analysis_audio_source"], "rendered_master_mix")
        self.assertIsNone(payload["analysis_audio_fallback_reason"])
        prepare_audio.assert_called_once()

    def test_waveform_ignores_cached_audio_when_signature_is_unavailable(self):
        fake_samples = np.ones(22050, dtype=np.float32)

        with (
            patch.object(waveform.resolve_service, "_connected", True),
            patch.object(waveform.resolve_service, "_resolve", object()),
            patch.object(waveform.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(
                waveform,
                "prepare_timeline_analysis_audio",
                return_value={
                    "audio_path": "/tmp/rendered.wav",
                    "fps": 24.0,
                    "timeline_start_frame": 100,
                    "timeline_end_frame": 340,
                    "analysis_audio_source": "rendered_master_mix",
                    "analysis_audio_fallback_reason": None,
                },
            ) as prepare_audio,
            patch("librosa.load", return_value=(fake_samples, 22050)),
            patch.object(
                waveform.manager,
                "send_progress",
                new=AsyncMock(),
            ),
        ):
            response = self.client.post("/api/waveform/analyze?samples=100")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sample_count"], 100)
        self.assertEqual(response.json()["analysis_audio_source"], "rendered_master_mix")
        prepare_audio.assert_called_once()

    def test_waveform_uses_legacy_cached_audio_when_master_mix_default_is_disabled(self):
        fake_samples = np.ones(22050, dtype=np.float32)

        with (
            patch.object(waveform.resolve_service, "_connected", True),
            patch.object(waveform.resolve_service, "_resolve", object()),
            patch.object(waveform.resolve_service, "get_current_timeline", return_value=_FakeTimeline()),
            patch.object(waveform.settings, "enable_master_mix_analysis_default", False),
            patch.object(
                waveform.resolve_service,
                "get_timeline_signature",
                return_value={"timeline_signature": "sig-123"},
            ),
            patch.object(
                waveform,
                "get_last_transcription_audio",
                return_value=("/tmp/cached.wav", 10.0, 0.0, "sig-123", True, "assembled_best_track"),
            ),
            patch.object(waveform.os.path, "exists", return_value=True),
            patch("librosa.load", return_value=(fake_samples, 22050)),
            patch.object(
                waveform.manager,
                "send_progress",
                new=AsyncMock(),
            ),
        ):
            response = self.client.post("/api/waveform/analyze?samples=100")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["sample_count"], 100)
        self.assertEqual(payload["analysis_audio_source"], "assembled_best_track")
        self.assertEqual(payload["analysis_audio_fallback_reason"], "master_mix_disabled")


if __name__ == "__main__":
    unittest.main()
