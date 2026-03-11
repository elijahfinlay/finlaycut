import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

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

    def _build_gap_feature_arrays(
        self,
        regions: list[tuple[float, float, str]],
        duration: float = 0.9,
        step: float = 0.02,
    ) -> dict[str, np.ndarray]:
        times = np.arange(0.0, duration, step, dtype=np.float32)
        arrays = {
            "times": times,
            "rms": np.full(times.shape, 0.03, dtype=np.float32),
            "flatness": np.full(times.shape, 0.58, dtype=np.float32),
            "centroid": np.full(times.shape, 2600.0, dtype=np.float32),
            "rolloff": np.full(times.shape, 4200.0, dtype=np.float32),
            "zcr": np.full(times.shape, 0.24, dtype=np.float32),
            "high_band_ratio": np.full(times.shape, 0.28, dtype=np.float32),
        }
        profiles = {
            "word": {
                "rms": 0.70,
                "flatness": 0.28,
                "centroid": 1720.0,
                "rolloff": 2920.0,
                "zcr": 0.12,
                "high_band_ratio": 0.16,
            },
            "candidate": {
                "rms": 0.88,
                "flatness": 0.16,
                "centroid": 1460.0,
                "rolloff": 2260.0,
                "zcr": 0.08,
                "high_band_ratio": 0.14,
            },
            "speechy_gap": {
                "rms": 0.48,
                "flatness": 0.26,
                "centroid": 1880.0,
                "rolloff": 3180.0,
                "zcr": 0.15,
                "high_band_ratio": 0.19,
            },
        }

        for start, end, profile_name in regions:
            values = profiles[profile_name]
            mask = (times >= start) & (times < end)
            if not np.any(mask):
                continue
            for key, value in values.items():
                arrays[key][mask] = value

        return arrays

    def _run_gap_detector_with_mocked_features(self, transcript: dict, features: dict[str, np.ndarray]) -> list[dict]:
        sample_rate = 16000
        y = np.zeros(sample_rate, dtype=np.float32)
        times = np.asarray(features["times"], dtype=np.float32)
        rms = np.asarray(features["rms"], dtype=np.float32)
        flatness = np.asarray(features["flatness"], dtype=np.float32)
        centroid = np.asarray(features["centroid"], dtype=np.float32)
        rolloff = np.asarray(features["rolloff"], dtype=np.float32)
        zcr = np.asarray(features["zcr"], dtype=np.float32)
        high_band_ratio = np.asarray(features["high_band_ratio"], dtype=np.float32)
        freqs = np.array([200.0, 700.0, 1400.0, 2400.0, 3600.0, 5200.0], dtype=np.float32)
        stft_matrix = np.zeros((len(freqs), len(times)), dtype=np.complex64)
        for idx in range(len(times)):
            total_power = max(0.01, float(rms[idx]) ** 2 * 8.0)
            desired_high_ratio = max(0.005, min(0.22, float(high_band_ratio[idx])))
            total_sum_power = total_power * len(freqs)
            high_sum_power = total_sum_power * (desired_high_ratio / 3.0)
            high_sum_power = min(high_sum_power, total_sum_power * 0.18)
            low_sum_power = max(total_sum_power - high_sum_power, total_sum_power * 0.60)
            stft_matrix[:4, idx] = np.sqrt(low_sum_power / 4.0)
            stft_matrix[4:, idx] = np.sqrt(high_sum_power / 2.0)

        with (
            patch("librosa.load", return_value=(y, sample_rate)),
            patch("librosa.stft", return_value=stft_matrix),
            patch("librosa.frames_to_time", return_value=times),
            patch("librosa.fft_frequencies", return_value=freqs),
            patch("librosa.feature.rms", return_value=np.array([rms], dtype=np.float32)),
            patch("librosa.feature.spectral_flatness", return_value=np.array([flatness], dtype=np.float32)),
            patch("librosa.feature.spectral_centroid", return_value=np.array([centroid], dtype=np.float32)),
            patch("librosa.feature.spectral_rolloff", return_value=np.array([rolloff], dtype=np.float32)),
            patch("librosa.feature.zero_crossing_rate", return_value=np.array([zcr], dtype=np.float32)),
        ):
            return audio_service.detect_hesitation_audio_candidates(
                "mock.wav",
                transcript,
                filler_words=["um", "uh"],
                sample_rate=sample_rate,
                hop_ms=10,
            )

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

    def test_detect_filler_words_keeps_contextual_words_out_of_strong_matches(self):
        transcript = {
            "words": [
                {"word": "So", "start": 0.00, "end": 0.24},
                {"word": "we", "start": 0.24, "end": 0.40},
                {"word": "um", "start": 0.52, "end": 0.67},
                {"word": "agree", "start": 0.70, "end": 1.02},
                {"word": "well", "start": 1.02, "end": 1.28},
            ]
        }

        detections = audio_service.detect_filler_words(
            transcript,
            filler_words=["um", "so", "well"],
        )

        self.assertEqual([d["word"].lower() for d in detections], ["um"])

    def test_detect_filler_words_matches_elongated_hesitations(self):
        transcript = {
            "words": [
                {"word": "ummm", "start": 0.00, "end": 0.18},
                {"word": "uhh", "start": 0.24, "end": 0.38},
                {"word": "okay", "start": 0.44, "end": 0.70},
            ]
        }

        detections = audio_service.detect_filler_words(transcript)

        self.assertEqual([d["word"].lower() for d in detections], ["ummm", "uhh"])

    def test_detect_filler_words_respects_requested_hesitation_families(self):
        transcript = {
            "words": [
                {"word": "erm", "start": 0.00, "end": 0.14},
                {"word": "uh", "start": 0.24, "end": 0.36},
                {"word": "ah", "start": 0.48, "end": 0.60},
                {"word": "um", "start": 0.74, "end": 0.92},
            ]
        }

        detections = audio_service.detect_filler_words(transcript, filler_words=["um", "uh"])

        self.assertEqual([d["word"].lower() for d in detections], ["uh", "um"])

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

    def test_estimate_audio_hesitation_label_prefers_um_for_nasal_profile(self):
        label = audio_service._estimate_audio_hesitation_label(
            {"um", "uh"},
            duration=0.24,
            avg_centroid=920.0,
            avg_rolloff=1700.0,
            avg_zcr=0.05,
            avg_high_band=0.07,
        )

        self.assertEqual(label, "um")

    def test_estimate_audio_hesitation_label_prefers_uh_for_open_vowel_profile(self):
        label = audio_service._estimate_audio_hesitation_label(
            {"um", "uh"},
            duration=0.11,
            avg_centroid=1580.0,
            avg_rolloff=2550.0,
            avg_zcr=0.12,
            avg_high_band=0.15,
        )

        self.assertEqual(label, "uh")

    def test_repetition_or_stutter_context_flags_repeated_or_prefixed_words(self):
        self.assertTrue(
            audio_service._is_repetition_or_stutter_context(
                {"word": "yeah"},
                {"word": "yeah"},
            )
        )
        self.assertTrue(
            audio_service._is_repetition_or_stutter_context(
                {"word": "ser"},
                {"word": "serving"},
            )
        )
        self.assertFalse(
            audio_service._is_repetition_or_stutter_context(
                {"word": "we"},
                {"word": "were"},
            )
        )
        self.assertTrue(
            audio_service._is_repetition_or_stutter_context(
                {"word": "st"},
                {"word": "students"},
            )
        )

    def test_repetition_or_stutter_context_allows_common_short_word_resets(self):
        self.assertFalse(
            audio_service._is_repetition_or_stutter_context(
                {"word": "in."},
                {"word": "In"},
            )
        )

    def test_matches_confident_hesitation_family_rejects_bright_noisy_candidate(self):
        self.assertFalse(
            audio_service._matches_confident_hesitation_family(
                "uh",
                duration=0.18,
                avg_centroid=2100.0,
                avg_rolloff=3400.0,
                avg_zcr=0.18,
                avg_high_band=0.21,
                avg_flatness=0.38,
            )
        )

    def test_matches_confident_hesitation_family_accepts_clean_um_profile(self):
        self.assertTrue(
            audio_service._matches_confident_hesitation_family(
                "um",
                duration=0.18,
                avg_centroid=1280.0,
                avg_rolloff=2100.0,
                avg_zcr=0.08,
                avg_high_band=0.09,
                avg_flatness=0.19,
            )
        )

    def test_detect_hesitation_audio_candidates_rejects_repetition_gap(self):
        samples = self._build_test_signal(
            [
                (0.00, 0.22, 0.76, 215.0),
                (0.24, 0.35, 0.24, 155.0),
                (0.42, 0.84, 0.80, 235.0),
            ],
            duration=0.9,
        )
        transcript = {
            "words": [
                {"word": "yeah", "start": 0.00, "end": 0.22},
                {"word": "yeah", "start": 0.42, "end": 0.84},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "repeat-gap.wav"
            self._write_wav(wav_path, samples)

            detections = audio_service.detect_hesitation_audio_candidates(
                str(wav_path),
                transcript,
                filler_words=["um", "uh"],
            )

        self.assertEqual(detections, [])

    def test_detect_hesitation_audio_candidates_recovers_centered_gap_hesitation(self):
        transcript = {
            "words": [
                {"word": "we", "start": 0.00, "end": 0.18},
                {"word": "agree", "start": 0.72, "end": 0.88},
            ]
        }
        features = self._build_gap_feature_arrays(
            [
                (0.00, 0.18, "word"),
                (0.36, 0.54, "candidate"),
                (0.72, 0.88, "word"),
            ]
        )

        detections = self._run_gap_detector_with_mocked_features(transcript, features)

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection["detection_source"], "audio_gap")
        self.assertEqual(detection["gap_edge_mode"], "center")
        self.assertIn(detection["word"], {"uh", "um"})

    def test_detect_hesitation_audio_candidates_recovers_left_edge_gap_hesitation(self):
        transcript = {
            "words": [
                {"word": "the", "start": 0.00, "end": 0.20},
                {"word": "students", "start": 0.72, "end": 0.88},
            ]
        }
        features = self._build_gap_feature_arrays(
            [
                (0.00, 0.20, "word"),
                (0.21, 0.39, "candidate"),
                (0.72, 0.88, "word"),
            ]
        )

        detections = self._run_gap_detector_with_mocked_features(transcript, features)

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection["gap_edge_mode"], "left")
        self.assertEqual(detection["detection_source"], "audio_gap")

    def test_detect_hesitation_audio_candidates_recovers_right_edge_gap_hesitation(self):
        transcript = {
            "words": [
                {"word": "we", "start": 0.00, "end": 0.22},
                {"word": "and", "start": 0.72, "end": 0.88},
            ]
        }
        features = self._build_gap_feature_arrays(
            [
                (0.00, 0.22, "word"),
                (0.51, 0.69, "candidate"),
                (0.72, 0.88, "word"),
            ]
        )

        detections = self._run_gap_detector_with_mocked_features(transcript, features)

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection["gap_edge_mode"], "right")
        self.assertEqual(detection["detection_source"], "audio_gap")

    def test_detect_hesitation_audio_candidates_rejects_edge_candidate_without_open_side_quiet(self):
        transcript = {
            "words": [
                {"word": "the", "start": 0.00, "end": 0.20},
                {"word": "students", "start": 0.72, "end": 0.88},
            ]
        }
        features = self._build_gap_feature_arrays(
            [
                (0.00, 0.20, "word"),
                (0.21, 0.39, "candidate"),
                (0.42, 0.60, "speechy_gap"),
                (0.72, 0.88, "word"),
            ]
        )

        detections = self._run_gap_detector_with_mocked_features(transcript, features)

        self.assertEqual(detections, [])

    def test_detect_hesitation_audio_candidates_rejects_weak_common_word_context(self):
        transcript = {
            "words": [
                {"word": "to", "start": 0.00, "end": 0.20},
                {"word": "put", "start": 0.50, "end": 0.72},
            ]
        }
        features = self._build_gap_feature_arrays(
            [
                (0.00, 0.20, "word"),
                (0.26, 0.43, "candidate"),
                (0.50, 0.72, "word"),
            ]
        )

        detections = self._run_gap_detector_with_mocked_features(transcript, features)

        self.assertEqual(detections, [])

    def test_detect_attached_hesitation_onsets_recovers_blended_uh(self):
        times = np.arange(0.0, 0.81, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        hesitation_mask = (times >= 0.28) & (times <= 0.40)
        speech_score[hesitation_mask] = 0.78
        dbfs[hesitation_mask] = -24.0
        flatness[hesitation_mask] = 0.16
        centroid[hesitation_mask] = 1450.0
        rolloff[hesitation_mask] = 2280.0
        zcr[hesitation_mask] = 0.10
        high_band_ratio[hesitation_mask] = 0.11

        continuation_mask = (times > 0.40) & (times <= 0.58)
        speech_score[continuation_mask] = 0.84
        dbfs[continuation_mask] = -19.0
        flatness[continuation_mask] = 0.22
        centroid[continuation_mask] = 1580.0
        rolloff[continuation_mask] = 2360.0
        zcr[continuation_mask] = 0.10
        high_band_ratio[continuation_mask] = 0.13

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.8,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "we", "start": 0.00, "end": 0.20, "end_frame": 5},
                {"word": "go", "start": 0.30, "end": 0.70, "start_frame": 7, "end_frame": 17},
            ]
        }

        detections = audio_service.detect_attached_hesitation_onsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection["word"], "uh")
        self.assertEqual(detection["attachment"], "word_onset")
        self.assertEqual(detection["attached_word"], "go")
        self.assertLess(detection["start"], 0.30)
        self.assertGreater(detection["end"], 0.36)
        self.assertLess(detection["end"], 0.46)
        self.assertEqual(detection["start_frame"], 7)
        self.assertGreater(detection["end_frame"], detection["start_frame"])

    def test_detect_attached_hesitation_onsets_rejects_mid_word_candidate(self):
        times = np.arange(0.0, 0.81, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        false_mask = (times >= 0.34) & (times <= 0.43)
        speech_score[false_mask] = 0.78
        dbfs[false_mask] = -24.0
        flatness[false_mask] = 0.16
        centroid[false_mask] = 1450.0
        rolloff[false_mask] = 2280.0
        zcr[false_mask] = 0.10
        high_band_ratio[false_mask] = 0.11

        continuation_mask = (times > 0.43) & (times <= 0.58)
        speech_score[continuation_mask] = 0.84
        dbfs[continuation_mask] = -19.0
        flatness[continuation_mask] = 0.22
        centroid[continuation_mask] = 1580.0
        rolloff[continuation_mask] = 2360.0
        zcr[continuation_mask] = 0.10
        high_band_ratio[continuation_mask] = 0.13

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.8,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "we", "start": 0.00, "end": 0.20},
                {"word": "going", "start": 0.30, "end": 0.70},
            ]
        }

        detections = audio_service.detect_attached_hesitation_onsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(detections, [])

    def test_detect_attached_hesitation_onsets_rejects_immediate_repetition_context(self):
        times = np.arange(0.0, 0.81, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        hesitation_mask = (times >= 0.28) & (times <= 0.40)
        speech_score[hesitation_mask] = 0.78
        dbfs[hesitation_mask] = -24.0
        flatness[hesitation_mask] = 0.16
        centroid[hesitation_mask] = 1450.0
        rolloff[hesitation_mask] = 2280.0
        zcr[hesitation_mask] = 0.10
        high_band_ratio[hesitation_mask] = 0.11

        continuation_mask = (times > 0.40) & (times <= 0.58)
        speech_score[continuation_mask] = 0.84
        dbfs[continuation_mask] = -19.0
        flatness[continuation_mask] = 0.22
        centroid[continuation_mask] = 1580.0
        rolloff[continuation_mask] = 2360.0
        zcr[continuation_mask] = 0.10
        high_band_ratio[continuation_mask] = 0.13

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.8,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "yeah", "start": 0.00, "end": 0.20},
                {"word": "yeah", "start": 0.30, "end": 0.70},
            ]
        }

        detections = audio_service.detect_attached_hesitation_onsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(detections, [])

    def test_detect_attached_hesitation_onsets_rejects_onset_without_quiet_lead_in(self):
        times = np.arange(0.0, 0.81, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        lead_in_mask = (times >= 0.25) & (times < 0.29)
        speech_score[lead_in_mask] = 0.74
        dbfs[lead_in_mask] = -23.0
        flatness[lead_in_mask] = 0.20
        centroid[lead_in_mask] = 1500.0
        rolloff[lead_in_mask] = 2300.0
        zcr[lead_in_mask] = 0.10
        high_band_ratio[lead_in_mask] = 0.12

        hesitation_mask = (times >= 0.29) & (times <= 0.40)
        speech_score[hesitation_mask] = 0.78
        dbfs[hesitation_mask] = -24.0
        flatness[hesitation_mask] = 0.16
        centroid[hesitation_mask] = 1450.0
        rolloff[hesitation_mask] = 2280.0
        zcr[hesitation_mask] = 0.10
        high_band_ratio[hesitation_mask] = 0.11

        continuation_mask = (times > 0.40) & (times <= 0.58)
        speech_score[continuation_mask] = 0.84
        dbfs[continuation_mask] = -19.0
        flatness[continuation_mask] = 0.22
        centroid[continuation_mask] = 1580.0
        rolloff[continuation_mask] = 2360.0
        zcr[continuation_mask] = 0.10
        high_band_ratio[continuation_mask] = 0.13

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.8,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "the", "start": 0.00, "end": 0.27},
                {"word": "students", "start": 0.30, "end": 0.70},
            ]
        }

        detections = audio_service.detect_attached_hesitation_onsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(detections, [])

    def test_detect_attached_hesitation_offsets_recovers_blended_uh_tail(self):
        times = np.arange(0.0, 0.91, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        lead_mask = (times >= 0.14) & (times <= 0.34)
        speech_score[lead_mask] = 0.84
        dbfs[lead_mask] = -19.0
        flatness[lead_mask] = 0.22
        centroid[lead_mask] = 1580.0
        rolloff[lead_mask] = 2360.0
        zcr[lead_mask] = 0.10
        high_band_ratio[lead_mask] = 0.13

        tail_mask = (times >= 0.38) & (times <= 0.50)
        speech_score[tail_mask] = 0.78
        dbfs[tail_mask] = -24.0
        flatness[tail_mask] = 0.16
        centroid[tail_mask] = 1450.0
        rolloff[tail_mask] = 2280.0
        zcr[tail_mask] = 0.10
        high_band_ratio[tail_mask] = 0.11

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.9,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "going", "start": 0.10, "end": 0.46, "start_frame": 2, "end_frame": 12},
                {"word": "there", "start": 0.58, "end": 0.84, "start_frame": 14, "end_frame": 20},
            ]
        }

        detections = audio_service.detect_attached_hesitation_offsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertIn(detection["word"], {"uh", "um"})
        self.assertEqual(detection["attachment"], "word_offset")
        self.assertEqual(detection["attached_word"], "going")
        self.assertGreater(detection["start"], 0.34)
        self.assertLess(detection["start"], 0.46)
        self.assertGreater(detection["end"], 0.45)
        self.assertLessEqual(detection["end"], 0.52)
        self.assertGreater(detection["end_frame"], detection["start_frame"])
        self.assertEqual(detection["detection_source"], "audio_attached_tail")

    def test_detect_attached_hesitation_offsets_recovers_larger_gap_tail_with_later_quiet_valley(self):
        times = np.arange(0.0, 0.97, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        lead_mask = (times >= 0.14) & (times <= 0.34)
        speech_score[lead_mask] = 0.84
        dbfs[lead_mask] = -19.0
        flatness[lead_mask] = 0.22
        centroid[lead_mask] = 1580.0
        rolloff[lead_mask] = 2360.0
        zcr[lead_mask] = 0.10
        high_band_ratio[lead_mask] = 0.13

        tail_mask = (times >= 0.38) & (times <= 0.50)
        speech_score[tail_mask] = 0.78
        dbfs[tail_mask] = -24.0
        flatness[tail_mask] = 0.16
        centroid[tail_mask] = 1450.0
        rolloff[tail_mask] = 2280.0
        zcr[tail_mask] = 0.10
        high_band_ratio[tail_mask] = 0.11

        hot_follow_mask = (times > 0.50) & (times <= 0.54)
        speech_score[hot_follow_mask] = 0.70
        dbfs[hot_follow_mask] = -23.5
        flatness[hot_follow_mask] = 0.22
        centroid[hot_follow_mask] = 1540.0
        rolloff[hot_follow_mask] = 2330.0
        zcr[hot_follow_mask] = 0.10
        high_band_ratio[hot_follow_mask] = 0.12

        quiet_valley_mask = (times >= 0.55) & (times <= 0.60)
        speech_score[quiet_valley_mask] = 0.12
        dbfs[quiet_valley_mask] = -57.0
        flatness[quiet_valley_mask] = 0.56
        centroid[quiet_valley_mask] = 2500.0
        rolloff[quiet_valley_mask] = 4000.0
        zcr[quiet_valley_mask] = 0.24
        high_band_ratio[quiet_valley_mask] = 0.26

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.96,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "going", "start": 0.10, "end": 0.46},
                {"word": "there", "start": 0.66, "end": 0.90},
            ]
        }

        detections = audio_service.detect_attached_hesitation_offsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["detection_source"], "audio_attached_tail")

    def test_detect_attached_hesitation_offsets_rejects_mid_word_tail_candidate(self):
        times = np.arange(0.0, 0.91, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        lead_mask = (times >= 0.14) & (times <= 0.22)
        speech_score[lead_mask] = 0.84
        dbfs[lead_mask] = -19.0
        flatness[lead_mask] = 0.22
        centroid[lead_mask] = 1580.0
        rolloff[lead_mask] = 2360.0
        zcr[lead_mask] = 0.10
        high_band_ratio[lead_mask] = 0.13

        mid_mask = (times >= 0.24) & (times <= 0.34)
        speech_score[mid_mask] = 0.78
        dbfs[mid_mask] = -24.0
        flatness[mid_mask] = 0.16
        centroid[mid_mask] = 1450.0
        rolloff[mid_mask] = 2280.0
        zcr[mid_mask] = 0.10
        high_band_ratio[mid_mask] = 0.11

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.9,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "going", "start": 0.10, "end": 0.60},
                {"word": "there", "start": 0.68, "end": 0.84},
            ]
        }

        detections = audio_service.detect_attached_hesitation_offsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(detections, [])

    def test_detect_attached_hesitation_offsets_rejects_near_zero_next_word_gap(self):
        times = np.arange(0.0, 0.81, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        lead_mask = (times >= 0.14) & (times <= 0.34)
        speech_score[lead_mask] = 0.84
        dbfs[lead_mask] = -19.0
        flatness[lead_mask] = 0.22
        centroid[lead_mask] = 1580.0
        rolloff[lead_mask] = 2360.0
        zcr[lead_mask] = 0.10
        high_band_ratio[lead_mask] = 0.13

        tail_mask = (times >= 0.38) & (times <= 0.50)
        speech_score[tail_mask] = 0.78
        dbfs[tail_mask] = -24.0
        flatness[tail_mask] = 0.16
        centroid[tail_mask] = 1450.0
        rolloff[tail_mask] = 2280.0
        zcr[tail_mask] = 0.10
        high_band_ratio[tail_mask] = 0.11

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.8,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "going", "start": 0.10, "end": 0.50},
                {"word": "there", "start": 0.52, "end": 0.74},
            ]
        }

        detections = audio_service.detect_attached_hesitation_offsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(detections, [])

    def test_detect_attached_hesitation_offsets_rejects_without_trailing_quiet_window(self):
        times = np.arange(0.0, 0.78, 0.01, dtype=np.float32)
        speech_score = np.full(times.shape, 0.08, dtype=np.float32)
        dbfs = np.full(times.shape, -58.0, dtype=np.float32)
        flatness = np.full(times.shape, 0.55, dtype=np.float32)
        centroid = np.full(times.shape, 2600.0, dtype=np.float32)
        rolloff = np.full(times.shape, 4200.0, dtype=np.float32)
        zcr = np.full(times.shape, 0.24, dtype=np.float32)
        high_band_ratio = np.full(times.shape, 0.28, dtype=np.float32)

        lead_mask = (times >= 0.14) & (times <= 0.34)
        speech_score[lead_mask] = 0.84
        dbfs[lead_mask] = -19.0
        flatness[lead_mask] = 0.22
        centroid[lead_mask] = 1580.0
        rolloff[lead_mask] = 2360.0
        zcr[lead_mask] = 0.10
        high_band_ratio[lead_mask] = 0.13

        tail_mask = (times >= 0.38) & (times <= 0.50)
        speech_score[tail_mask] = 0.78
        dbfs[tail_mask] = -24.0
        flatness[tail_mask] = 0.16
        centroid[tail_mask] = 1450.0
        rolloff[tail_mask] = 2280.0
        zcr[tail_mask] = 0.10
        high_band_ratio[tail_mask] = 0.11

        noisy_follow_mask = (times > 0.50) & (times <= 0.54)
        speech_score[noisy_follow_mask] = 0.74
        dbfs[noisy_follow_mask] = -22.0
        flatness[noisy_follow_mask] = 0.20
        centroid[noisy_follow_mask] = 1500.0
        rolloff[noisy_follow_mask] = 2320.0
        zcr[noisy_follow_mask] = 0.10
        high_band_ratio[noisy_follow_mask] = 0.12

        profile = {
            "times": times,
            "speech_score": speech_score,
            "dbfs": dbfs,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "high_band_ratio": high_band_ratio,
            "duration": 0.77,
            "hop_seconds": 0.01,
        }
        transcript = {
            "words": [
                {"word": "going", "start": 0.10, "end": 0.50},
                {"word": "there", "start": 0.55, "end": 0.74},
            ]
        }

        detections = audio_service.detect_attached_hesitation_offsets(
            transcript,
            filler_words=["uh", "um"],
            profile=profile,
        )

        self.assertEqual(detections, [])


if __name__ == "__main__":
    unittest.main()
