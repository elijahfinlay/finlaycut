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


class FillerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_analyze_uses_cached_transcription_audio_when_available(self):
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
                return_value=("/tmp/cached-transcript.wav", 33.4, 0.0, "sig-123", False),
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
        self.assertEqual(payload["detections"][0]["start_frame"], 340)
        build_profile.assert_called_once_with("/tmp/cached-transcript.wav")
        analyze_candidates.assert_called_once()

    def test_analyze_ignores_cached_audio_when_transcript_starts_before_cached_offset(self):
        transcript = {
            "words": [
                {"word": "um", "start": 0.2, "end": 0.4, "probability": 0.6},
                {"word": "hello", "start": 0.5, "end": 0.8, "probability": 0.9},
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
                return_value=("/tmp/cached-transcript.wav", 5.0, 10.0, "sig-123", False),
            ),
            patch.object(filler_words.os.path, "exists", return_value=True),
            patch.object(
                filler_words,
                "assemble_current_timeline_audio",
                return_value=("/tmp/assembled.wav", 24.0, 100, 300),
            ) as assemble_audio,
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
        assemble_audio.assert_called_once()


if __name__ == "__main__":
    unittest.main()
