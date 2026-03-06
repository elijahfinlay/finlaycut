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
        render_audio.assert_called_once_with(
            "finlaycut_transcript",
            mark_in_frame=None,
            mark_out_frame=None,
            fallback_restore_page="edit",
        )


if __name__ == "__main__":
    unittest.main()
