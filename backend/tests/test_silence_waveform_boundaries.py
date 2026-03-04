import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from services.audio_service import audio_service


class SilenceWaveformBoundaryTests(unittest.TestCase):
    def _write_wav(self, path: Path, samples: np.ndarray, sample_rate: int = 16000) -> None:
        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm.tobytes())

    def _build_test_signal(self, regions: list[tuple[float, float, float, float]], duration: float = 1.0) -> np.ndarray:
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

    def test_refine_silence_segments_to_waveform_trims_to_dead_air_center(self):
        samples = self._build_test_signal(
            [
                (0.00, 0.28, 0.80, 220.0),
                (0.58, 0.92, 0.84, 250.0),
            ],
            duration=1.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "silence-center.wav"
            self._write_wav(wav_path, samples)

            refined = audio_service.refine_silence_segments_to_waveform(
                str(wav_path),
                [{
                    "start": 0.30,
                    "end": 0.55,
                    "duration": 0.25,
                    "kind": "silence",
                }],
                preserve_padding_ms=0,
            )

        self.assertEqual(len(refined), 1)
        self.assertEqual(refined[0]["boundary_source"], "waveform")
        self.assertGreaterEqual(refined[0]["start"], 0.34)
        self.assertLessEqual(refined[0]["end"], 0.53)
        self.assertGreater(refined[0]["duration"], 0.04)

    def test_refine_silence_segments_to_waveform_skips_too_short_pause_after_floor(self):
        samples = self._build_test_signal(
            [
                (0.00, 0.30, 0.80, 220.0),
                (0.38, 0.90, 0.82, 240.0),
            ],
            duration=1.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "too-short-silence.wav"
            self._write_wav(wav_path, samples)

            refined = audio_service.refine_silence_segments_to_waveform(
                str(wav_path),
                [{
                    "start": 0.30,
                    "end": 0.38,
                    "duration": 0.08,
                    "kind": "silence",
                }],
                preserve_padding_ms=0,
            )

        self.assertEqual(refined, [])

    def test_refine_silence_segments_to_waveform_applies_natural_pause_floor_with_zero_padding(self):
        samples = self._build_test_signal(
            [
                (0.00, 0.24, 0.82, 210.0),
                (0.50, 0.92, 0.86, 245.0),
            ],
            duration=1.0,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "natural-pause-floor.wav"
            self._write_wav(wav_path, samples)

            full_silence = audio_service.refine_silence_segments_to_waveform(
                str(wav_path),
                [{
                    "start": 0.26,
                    "end": 0.48,
                    "duration": 0.22,
                    "kind": "silence",
                }],
                preserve_padding_ms=None,
            )
            trimmed = audio_service.refine_silence_segments_to_waveform(
                str(wav_path),
                [{
                    "start": 0.26,
                    "end": 0.48,
                    "duration": 0.22,
                    "kind": "silence",
                }],
                preserve_padding_ms=0,
            )

        self.assertEqual(len(full_silence), 1)
        self.assertEqual(len(trimmed), 1)
        full_duration = full_silence[0]["duration"]
        trimmed_duration = trimmed[0]["duration"]
        self.assertLess(trimmed_duration, full_duration)
        self.assertAlmostEqual(trimmed_duration, full_duration - 0.12, delta=0.03)


if __name__ == "__main__":
    unittest.main()
