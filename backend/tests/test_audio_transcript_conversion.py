import unittest

from services.audio_service import audio_service


class AudioTranscriptConversionTests(unittest.TestCase):
    def test_convert_result_synthesizes_words_from_segment_text(self):
        result = {
            "segments": [
                {
                    "start": 1.0,
                    "end": 2.5,
                    "text": "hello world again",
                }
            ],
            "language": "en",
        }

        transcript = audio_service._convert_result(result, "en")

        self.assertEqual([word["word"] for word in transcript["words"]], ["hello", "world", "again"])
        self.assertEqual(transcript["segments"][0]["words"][0]["word"], "hello")
        self.assertGreater(transcript["words"][1]["start"], transcript["words"][0]["start"])
        self.assertGreater(transcript["words"][2]["end"], transcript["words"][1]["end"])


if __name__ == "__main__":
    unittest.main()
