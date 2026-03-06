import asyncio
import unittest

from services.ai_service import ai_service


class RepetitionDetectionTests(unittest.TestCase):
    def _make_take(
        self,
        text: str,
        start: float,
        probability: float = 0.95,
        step: float = 0.1,
    ) -> tuple[list[dict], dict]:
        words: list[dict] = []
        cursor = start
        for token in text.split():
            word_start = round(cursor, 3)
            word_end = round(cursor + step, 3)
            words.append({
                "word": token,
                "start": word_start,
                "end": word_end,
                "probability": probability,
            })
            cursor = word_end

        segment = {
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": text,
        }
        return words, segment

    def _detect(self, takes: list[tuple[str, float, float]]) -> list[dict]:
        words: list[dict] = []
        segments: list[dict] = []
        for text, start, probability in takes:
            take_words, segment = self._make_take(text, start, probability=probability)
            words.extend(take_words)
            segments.append(segment)

        return asyncio.run(ai_service.detect_repetitions({
            "words": words,
            "segments": segments,
        }))

    def test_detect_repetitions_handles_mid_take_restart(self):
        groups = self._detect([
            ("well so today we're going to", 0.0, 0.90),
            ("okay so today we're going to talk about x", 1.3, 0.97),
        ])

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["matched_text"], "so today we're going to")
        self.assertEqual(group["best_index"], 1)
        self.assertEqual(group["takes"][0]["text"], "well so today we're going to")
        self.assertEqual(group["takes"][1]["text"], "okay so today we're going to talk about x")
        self.assertGreater(
            group["takes"][1]["scores"]["combined"],
            group["takes"][0]["scores"]["combined"],
        )

    def test_detect_repetitions_groups_three_retakes(self):
        groups = self._detect([
            ("we need to ship", 0.0, 0.91),
            ("we need to ship this", 1.0, 0.94),
            ("we need to ship this week", 2.0, 0.98),
        ])

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["count"], 3)
        self.assertEqual(group["matched_text"], "we need to ship")
        self.assertEqual([take["text"] for take in group["takes"]], [
            "we need to ship",
            "we need to ship this",
            "we need to ship this week",
        ])
        self.assertEqual(group["best_index"], 2)

    def test_detect_repetitions_does_not_merge_unrelated_edges(self):
        groups = self._detect([
            ("we need to ship today", 0.0, 0.93),
            ("we need to ship today and hire fast now", 1.0, 0.95),
            ("hire fast now please", 3.0, 0.96),
        ])

        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["matched_text"], "we need to ship today")
        self.assertEqual([take["text"] for take in group["takes"]], [
            "we need to ship today",
            "we need to ship today and hire fast now",
        ])

    def test_detect_repetitions_preserves_take_frame_boundaries(self):
        words = [
            {"word": "we", "start": 0.0, "end": 0.1, "start_frame": 100, "end_frame": 102, "probability": 0.9},
            {"word": "need", "start": 0.1, "end": 0.2, "start_frame": 102, "end_frame": 104, "probability": 0.9},
            {"word": "to", "start": 0.2, "end": 0.3, "start_frame": 104, "end_frame": 106, "probability": 0.9},
            {"word": "ship", "start": 0.3, "end": 0.4, "start_frame": 106, "end_frame": 108, "probability": 0.9},
            {"word": "we", "start": 1.0, "end": 1.1, "start_frame": 124, "end_frame": 126, "probability": 0.95},
            {"word": "need", "start": 1.1, "end": 1.2, "start_frame": 126, "end_frame": 128, "probability": 0.95},
            {"word": "to", "start": 1.2, "end": 1.3, "start_frame": 128, "end_frame": 130, "probability": 0.95},
            {"word": "ship", "start": 1.3, "end": 1.4, "start_frame": 130, "end_frame": 132, "probability": 0.95},
            {"word": "now", "start": 1.4, "end": 1.5, "start_frame": 132, "end_frame": 134, "probability": 0.95},
        ]

        groups = asyncio.run(ai_service.detect_repetitions({"words": words, "segments": []}))

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["takes"][0]["start_frame"], 100)
        self.assertEqual(groups[0]["takes"][0]["end_frame"], 108)
        self.assertEqual(groups[0]["takes"][1]["start_frame"], 124)
        self.assertEqual(groups[0]["takes"][1]["end_frame"], 134)


if __name__ == "__main__":
    unittest.main()
