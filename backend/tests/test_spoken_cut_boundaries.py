import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from services.audio_service import audio_service


class SpokenCutBoundaryTests(unittest.TestCase):
    def _write_wav(self, path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())

    def _build_signal(self, regions: list[tuple[float, float, float, float]], duration: float = 1.0) -> np.ndarray:
        sample_rate = 16000
        total_samples = int(sample_rate * duration)
        samples = np.zeros(total_samples, dtype=np.float32)
        times = np.arange(total_samples, dtype=np.float32) / sample_rate

        for start, end, amplitude, frequency in regions:
            mask = (times >= start) & (times < end)
            if not np.any(mask):
                continue
            phase = 2.0 * np.pi * frequency * times[mask]
            samples[mask] += amplitude * np.sin(phase)

        return samples

    def test_spoken_refiner_expands_tight_seed_bidirectionally(self):
        samples = self._build_signal(
            [
                (0.00, 0.16, 0.60, 170.0),
                (0.20, 0.42, 0.85, 220.0),
                (0.50, 0.82, 0.78, 260.0),
            ],
            duration=0.95,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "spoken-tight-seed.wav"
            self._write_wav(wav_path, samples)
            refined = audio_service.refine_spoken_segments_to_waveform(
                str(wav_path),
                [{
                    "kind": "speech",
                    "start": 0.25,
                    "end": 0.33,
                    "prev_word_end": 0.18,
                    "next_word_start": 0.50,
                }],
                left_search_ms=160,
                right_search_ms=220,
                start_guard_ms=8,
                end_guard_ms=28,
                low_confidence_max_expand_ms=20,
            )

        self.assertEqual(len(refined), 1)
        self.assertEqual(refined[0]["boundary_source"], "waveform")
        self.assertLess(refined[0]["start"], 0.25)
        self.assertGreater(refined[0]["end"], 0.33)
        self.assertGreaterEqual(refined[0]["start"], 0.18)
        self.assertLessEqual(refined[0]["end"], 0.472)
        self.assertFalse(refined[0]["used_fallback_seed"])
        self.assertIsNotNone(refined[0]["refine_confidence"])

    def test_spoken_refiner_uses_seed_when_acoustic_confidence_is_weak(self):
        samples = self._build_signal(
            [
                (0.00, 0.18, 0.55, 190.0),
                (0.54, 0.78, 0.66, 230.0),
            ],
            duration=0.9,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "spoken-weak-confidence.wav"
            self._write_wav(wav_path, samples)
            refined = audio_service.refine_spoken_segments_to_waveform(
                str(wav_path),
                [{
                    "kind": "script",
                    "start": 0.30,
                    "end": 0.38,
                }],
                left_search_ms=120,
                right_search_ms=160,
                fallback_confidence_threshold=0.40,
            )

        self.assertEqual(len(refined), 1)
        self.assertIn(refined[0]["boundary_source"], {"seed", "waveform"})
        self.assertLessEqual(refined[0]["start"], 0.30)
        self.assertGreaterEqual(refined[0]["start"], 0.18)
        self.assertAlmostEqual(refined[0]["end"], 0.38, places=4)
        self.assertIn(refined[0]["refine_reason"], {"weak_acoustic_evidence", "low_confidence_capped_expand"})

    def test_spoken_refiner_applies_more_conservative_right_guard(self):
        samples = self._build_signal(
            [
                (0.00, 0.17, 0.58, 170.0),
                (0.20, 0.40, 0.78, 210.0),
                (0.44, 0.74, 0.82, 260.0),
            ],
            duration=0.85,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "spoken-right-guard.wav"
            self._write_wav(wav_path, samples)
            refined = audio_service.refine_spoken_segments_to_waveform(
                str(wav_path),
                [{
                    "kind": "retake",
                    "start": 0.23,
                    "end": 0.34,
                    "prev_word_end": 0.18,
                    "next_word_start": 0.44,
                }],
                left_search_ms=160,
                right_search_ms=220,
                start_guard_ms=10,
                end_guard_ms=40,
            )

        self.assertEqual(len(refined), 1)
        self.assertLess(refined[0]["start"], 0.23)
        self.assertLessEqual(refined[0]["end"], 0.40)
        self.assertGreater(refined[0]["end"], 0.34)
        self.assertIn(
            refined[0]["refine_reason"],
            {"continuation_component", "component_overlap", "low_confidence_capped_expand", "seed_preserving_component"},
        )

    def test_spoken_refiner_does_not_collapse_seed_to_short_internal_component(self):
        samples = self._build_signal(
            [
                (0.16, 0.56, 0.42, 180.0),
                (0.28, 0.36, 0.82, 260.0),
                (0.36, 0.54, 0.48, 210.0),
            ],
            duration=0.8,
        )

        seed_start = 0.20
        seed_end = 0.50

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "spoken-no-internal-collapse.wav"
            self._write_wav(wav_path, samples)
            refined = audio_service.refine_spoken_segments_to_waveform(
                str(wav_path),
                [{
                    "kind": "speech",
                    "start": seed_start,
                    "end": seed_end,
                    "prev_word_end": 0.12,
                    "next_word_start": 0.60,
                }],
                left_search_ms=180,
                right_search_ms=220,
                start_guard_ms=8,
                end_guard_ms=28,
            )

        self.assertEqual(len(refined), 1)
        self.assertFalse(refined[0]["used_fallback_seed"])
        self.assertLessEqual(refined[0]["start"], seed_start + 0.02)
        self.assertGreaterEqual(refined[0]["end"], seed_end - 0.02)
        self.assertGreaterEqual(refined[0]["duration"], 0.24)


if __name__ == "__main__":
    unittest.main()
