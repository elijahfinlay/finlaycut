import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from services.audio_service import audio_service


class SilenceMasterMixAnalysisTests(unittest.TestCase):
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

    def test_master_mix_silence_rejects_music_bed_false_positive(self):
        samples = self._build_signal(
            [
                (0.00, 0.90, 0.22, 330.0),
            ],
            duration=0.9,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "music-bed.wav"
            self._write_wav(wav_path, samples)
            refined = audio_service.refine_silence_segments_to_waveform(
                str(wav_path),
                [{"start": 0.25, "end": 0.55, "kind": "silence"}],
                min_duration_ms=80,
                require_dialogue_valley=True,
            )

        self.assertEqual(refined, [])

    def test_master_mix_silence_keeps_room_tone_pause_between_dialogue_bursts(self):
        samples = self._build_signal(
            [
                (0.00, 0.90, 0.03, 120.0),
                (0.00, 0.18, 0.66, 220.0),
                (0.42, 0.78, 0.74, 260.0),
            ],
            duration=0.9,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "room-tone-pause.wav"
            self._write_wav(wav_path, samples)
            refined = audio_service.refine_silence_segments_to_waveform(
                str(wav_path),
                [{"start": 0.18, "end": 0.42, "kind": "silence"}],
                min_duration_ms=80,
                require_dialogue_valley=True,
            )

        self.assertEqual(len(refined), 1)
        self.assertEqual(refined[0]["boundary_source"], "waveform")
        self.assertIn(refined[0]["refine_reason"], {"validated_dialogue_valley", "validated_speech_contrast"})
        self.assertFalse(refined[0]["used_fallback_seed"])

    def test_master_mix_silence_rejects_stacked_audio_without_clear_dialogue_valley(self):
        samples = self._build_signal(
            [
                (0.00, 0.90, 0.18, 140.0),
                (0.00, 0.90, 0.17, 360.0),
                (0.00, 0.20, 0.55, 220.0),
                (0.52, 0.88, 0.60, 250.0),
            ],
            duration=0.9,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "stacked-audio.wav"
            self._write_wav(wav_path, samples)
            refined = audio_service.refine_silence_segments_to_waveform(
                str(wav_path),
                [{"start": 0.22, "end": 0.48, "kind": "silence"}],
                min_duration_ms=80,
                require_dialogue_valley=True,
            )

        self.assertEqual(refined, [])


if __name__ == "__main__":
    unittest.main()
