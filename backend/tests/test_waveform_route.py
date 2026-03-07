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
                waveform.resolve_service,
                "get_timeline_signature",
                return_value={"timeline_signature": "sig-123"},
            ),
            patch.object(
                waveform,
                "get_last_transcription_audio",
                return_value=("/tmp/cached.wav", 5.0, 8.0, "sig-123", False),
            ),
            patch.object(waveform.os.path, "exists", return_value=True),
            patch.object(
                waveform.resolve_service,
                "get_timeline_audio_clip_contexts",
                return_value={
                    "contexts": [],
                    "has_any_items": True,
                    "attempts": ["compound-clip"],
                },
            ),
            patch.object(
                waveform.resolve_service,
                "render_current_timeline_audio",
                return_value="/tmp/rendered.wav",
            ) as render_audio,
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
        render_audio.assert_called_once()


if __name__ == "__main__":
    unittest.main()
