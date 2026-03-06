import unittest

from fastapi.testclient import TestClient

from main import app


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


if __name__ == "__main__":
    unittest.main()
