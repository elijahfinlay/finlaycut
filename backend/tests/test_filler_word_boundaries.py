import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from api.routes.filler_words import _adjust_filler_cut_frame_range, _clamp_filler_cut_frame_range
from services.audio_service import audio_service


class FillerWordBoundaryTests(unittest.TestCase):
    def _write_wav(self, path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())

    def _build_test_signal(self, regions: list[tuple[float, float, float, float]], duration: float = 0.9) -> np.ndarray:
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

    def test_detect_filler_words_attaches_neighbor_guards(self):
        transcript = {
            "words": [
                {"word": "we", "start": 0.0, "end": 0.18, "end_frame": 4},
                {"word": "um", "start": 0.18, "end": 0.34, "start_frame": 4, "end_frame": 8},
                {"word": "agree", "start": 0.36, "end": 0.72, "start_frame": 9, "end_frame": 17},
            ]
        }

        detections = audio_service.detect_filler_words(transcript, filler_words=["um"])

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection["prev_word"], "we")
        self.assertEqual(detection["prev_word_end"], 0.18)
        self.assertEqual(detection["prev_word_end_frame"], 4)
        self.assertEqual(detection["next_word"], "agree")
        self.assertEqual(detection["next_word_start"], 0.36)
        self.assertEqual(detection["next_word_start_frame"], 9)

    def test_clamp_filler_cut_range_reserves_neighbor_word_frames(self):
        clamped = _clamp_filler_cut_frame_range(
            start_frame=23,
            end_frame=31,
            filler={
                "prev_word_end_frame": 24,
                "next_word_start_frame": 31,
            },
            timeline_start_frame=0,
            timeline_end_frame=240,
            fps=24.0,
        )

        self.assertEqual(clamped, (24, 30))

    def test_adjust_filler_cut_keeps_full_refined_range(self):
        adjusted = _adjust_filler_cut_frame_range(100, 105)

        self.assertEqual(adjusted, (100, 105))

    def test_refine_segments_to_low_energy_uses_neighbor_word_boundary(self):
        sample_rate = 16000
        samples = self._build_test_signal(
            [
                (0.00, 0.18, 0.65, 220.0),
                (0.18, 0.34, 0.22, 170.0),
                (0.36, 0.72, 0.80, 260.0),
            ],
            duration=0.8,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "filler-boundary.wav"
            self._write_wav(wav_path, samples, sample_rate=sample_rate)

            refined = audio_service.refine_segments_to_low_energy(
                str(wav_path),
                [{
                    "start": 0.18,
                    "end": 0.40,
                    "prev_word_end": 0.18,
                    "next_word_start": 0.36,
                }],
                max_shift_ms=120,
                min_duration_ms=20,
            )

        self.assertEqual(len(refined), 1)
        self.assertGreaterEqual(refined[0]["start"], 0.18)
        self.assertLessEqual(refined[0]["end"], 0.36)

    def test_refine_filler_segments_to_waveform_expands_from_tight_seed(self):
        samples = self._build_test_signal(
            [
                (0.00, 0.18, 0.72, 220.0),
                (0.20, 0.34, 0.28, 170.0),
                (0.38, 0.74, 0.82, 260.0),
            ],
            duration=0.9,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "tight-seed.wav"
            self._write_wav(wav_path, samples)

            refined = audio_service.refine_filler_segments_to_waveform(
                str(wav_path),
                [{
                    "word": "um",
                    "start": 0.22,
                    "end": 0.30,
                    "prev_word_end": 0.18,
                    "next_word_start": 0.38,
                }],
            )

        self.assertEqual(len(refined), 1)
        self.assertEqual(refined[0]["boundary_source"], "waveform")
        self.assertLess(refined[0]["start"], 0.22)
        self.assertGreater(refined[0]["end"], 0.30)
        self.assertGreaterEqual(refined[0]["start"], 0.18)
        self.assertLessEqual(refined[0]["end"], 0.38)

    def test_refine_filler_segments_to_waveform_stops_before_next_word(self):
        samples = self._build_test_signal(
            [
                (0.00, 0.18, 0.68, 220.0),
                (0.20, 0.31, 0.26, 180.0),
                (0.33, 0.72, 0.84, 270.0),
            ],
            duration=0.85,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "next-word-guard.wav"
            self._write_wav(wav_path, samples)

            refined = audio_service.refine_filler_segments_to_waveform(
                str(wav_path),
                [{
                    "word": "uh",
                    "start": 0.21,
                    "end": 0.34,
                    "prev_word_end": 0.18,
                    "next_word_start": 0.33,
                }],
            )

        self.assertEqual(len(refined), 1)
        self.assertEqual(refined[0]["boundary_source"], "waveform")
        self.assertLessEqual(refined[0]["end"], 0.33)
        self.assertGreater(refined[0]["end"], 0.29)

    def test_refine_audio_only_hesitation_uses_waveform_boundaries(self):
        samples = self._build_test_signal(
            [
                (0.00, 0.18, 0.70, 220.0),
                (0.42, 0.55, 0.25, 150.0),
                (0.60, 0.86, 0.83, 250.0),
            ],
            duration=0.95,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "audio-only-hesitation.wav"
            self._write_wav(wav_path, samples)

            refined = audio_service.refine_filler_segments_to_waveform(
                str(wav_path),
                [{
                    "word": "[filler]",
                    "start": 0.45,
                    "end": 0.51,
                    "context": "between words",
                    "kind": "audio_hesitation",
                }],
            )

        self.assertEqual(len(refined), 1)
        self.assertEqual(refined[0]["boundary_source"], "waveform")
        self.assertLess(refined[0]["start"], 0.45)
        self.assertGreater(refined[0]["end"], 0.51)


if __name__ == "__main__":
    unittest.main()
