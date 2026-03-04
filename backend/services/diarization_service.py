"""Speaker diarization service: identify who is speaking when."""

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

import numpy as np

logger = logging.getLogger("finlaycut.diarization")
PYANNOTE_MODEL_ID = "pyannote/speaker-diarization-3.1"
PYANNOTE_CONFIG_FILE = "config.yaml"


def _stft_window_params(sr: int, hop_seconds: float, minimum_n_fft: int = 1024) -> tuple[int, int, int]:
    hop_length = max(1, int(sr * hop_seconds))
    win_length = max(512, hop_length * 4)
    n_fft = max(int(minimum_n_fft), 1)
    while n_fft < win_length:
        n_fft *= 2
    return hop_length, win_length, n_fft


class DiarizationService:
    def __init__(self):
        self._pipeline = None
        self._pipeline_error: str | None = None

    def _remote_diarization_disabled(self) -> bool:
        value = os.getenv("FINLAYCUT_DISABLE_REMOTE_DIARIZATION", "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _resolve_hf_token(self) -> str | None:
        for env_name in ("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            token = os.getenv(env_name)
            if token:
                return token

        try:
            from huggingface_hub import HfFolder

            token = HfFolder.get_token()
            if token:
                return token
        except Exception:
            pass

        return None

    def _has_cached_pyannote_model(self) -> bool:
        try:
            from huggingface_hub import try_to_load_from_cache

            cached_path = try_to_load_from_cache(PYANNOTE_MODEL_ID, PYANNOTE_CONFIG_FILE)
            return isinstance(cached_path, str) and Path(cached_path).exists()
        except Exception:
            return False

    def _load_pipeline(self):
        """Load the diarization pipeline (pyannote.audio)."""
        if self._pipeline is not None:
            return
        if self._pipeline_error is not None:
            raise RuntimeError(self._pipeline_error)

        if self._remote_diarization_disabled():
            self._pipeline_error = "Remote diarization disabled by FINLAYCUT_DISABLE_REMOTE_DIARIZATION"
            raise RuntimeError(self._pipeline_error)

        try:
            from pyannote.audio import Pipeline
            import torch

            token = self._resolve_hf_token()
            has_cached_model = self._has_cached_pyannote_model()
            if not token and not has_cached_model:
                self._pipeline_error = (
                    "Pyannote diarization model is gated and no Hugging Face token or local cache is available."
                )
                raise RuntimeError(self._pipeline_error)

            pipeline_kwargs = {}
            if token:
                pipeline_kwargs["token"] = token
            elif has_cached_model:
                pipeline_kwargs["local_files_only"] = True

            self._pipeline = Pipeline.from_pretrained(PYANNOTE_MODEL_ID, **pipeline_kwargs)
            if torch.backends.mps.is_available():
                self._pipeline.to(torch.device("mps"))
            elif torch.cuda.is_available():
                self._pipeline.to(torch.device("cuda"))

            logger.info("Loaded pyannote diarization pipeline")
        except ImportError:
            self._pipeline_error = (
                "Speaker diarization requires pyannote.audio. "
                "Install with: pip install pyannote.audio"
            )
            raise ImportError(
                "Speaker diarization requires pyannote.audio. "
                "Install with: pip install pyannote.audio"
            )
        except Exception as e:
            self._pipeline_error = str(e)
            logger.error("Failed to load diarization pipeline: %s", e)
            raise

    def _format_segments(self, segments: list[dict]) -> list[dict]:
        formatted = [
            {
                "speaker": str(seg.get("speaker")),
                "start": round(float(seg.get("start", 0.0)), 3),
                "end": round(float(seg.get("end", 0.0)), 3),
                "duration": round(max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))), 3),
            }
            for seg in segments
            if float(seg.get("end", 0.0)) > float(seg.get("start", 0.0))
        ]

        formatted.sort(key=lambda seg: (seg["start"], seg["end"]))
        speakers = list(dict.fromkeys(seg["speaker"] for seg in formatted))
        speaker_map = {name: index for index, name in enumerate(speakers)}
        for seg in formatted:
            seg["speaker_index"] = speaker_map[seg["speaker"]]
        return formatted

    def _load_waveform(self, audio_path: str, sample_rate: int = 16000) -> tuple[np.ndarray, int]:
        import librosa

        waveform, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
        if waveform.ndim > 1:
            waveform = np.mean(waveform, axis=0)
        return waveform.astype(np.float32, copy=False), int(sr)

    def _extract_intervals(
        self,
        mask: np.ndarray,
        times: np.ndarray,
        hop_seconds: float,
        min_frames: int,
        merge_gap_frames: int,
    ) -> list[dict]:
        intervals: list[dict] = []
        run_start = None

        for index, flag in enumerate(mask):
            if flag and run_start is None:
                run_start = index
            elif not flag and run_start is not None:
                if index - run_start >= min_frames:
                    end_index = min(len(times) - 1, index - 1)
                    start_time = float(times[run_start])
                    end_time = float(times[end_index] + hop_seconds)
                    intervals.append({
                        "start": round(start_time, 4),
                        "end": round(end_time, 4),
                        "duration": round(max(0.0, end_time - start_time), 4),
                    })
                run_start = None

        if run_start is not None and len(times) - run_start >= min_frames:
            start_time = float(times[run_start])
            end_time = float(times[-1] + hop_seconds)
            intervals.append({
                "start": round(start_time, 4),
                "end": round(end_time, 4),
                "duration": round(max(0.0, end_time - start_time), 4),
            })

        if not intervals:
            return []

        merged = [intervals[0]]
        max_gap = max(0.0, float(merge_gap_frames) * hop_seconds)
        for interval in intervals[1:]:
            previous = merged[-1]
            if interval["start"] - previous["end"] <= max_gap:
                previous["end"] = round(max(previous["end"], interval["end"]), 4)
                previous["duration"] = round(previous["end"] - previous["start"], 4)
            else:
                merged.append(interval)
        return merged

    def _robust_scale(self, values: np.ndarray) -> np.ndarray:
        if values.size == 0:
            return values
        low = np.percentile(values, 10, axis=0)
        high = np.percentile(values, 90, axis=0)
        spread = np.where(np.abs(high - low) < 1e-6, 1.0, high - low)
        return (values - low) / spread

    def _init_kmeans_centers(self, features: np.ndarray, k: int) -> np.ndarray:
        if len(features) == 0:
            return np.empty((0, 0), dtype=np.float32)

        chosen = [int(np.argmax(np.linalg.norm(features, axis=1)))]
        while len(chosen) < k:
            centers = features[chosen]
            distances = np.sum((features[:, None, :] - centers[None, :, :]) ** 2, axis=2)
            next_index = int(np.argmax(np.min(distances, axis=1)))
            if next_index in chosen:
                remaining = [index for index in range(len(features)) if index not in chosen]
                if not remaining:
                    break
                next_index = remaining[0]
            chosen.append(next_index)
        return features[chosen]

    def _kmeans(self, features: np.ndarray, k: int, max_iter: int = 40) -> tuple[np.ndarray, np.ndarray, float]:
        if len(features) == 0:
            return np.array([], dtype=np.int32), np.empty((0, 0), dtype=np.float32), 0.0

        k = max(1, min(int(k), len(features)))
        centers = self._init_kmeans_centers(features, k)
        labels = np.zeros(len(features), dtype=np.int32)

        for _ in range(max_iter):
            distances = np.sum((features[:, None, :] - centers[None, :, :]) ** 2, axis=2)
            next_labels = np.argmin(distances, axis=1).astype(np.int32)
            next_centers = centers.copy()
            for cluster in range(k):
                members = features[next_labels == cluster]
                if len(members) == 0:
                    farthest_index = int(np.argmax(np.min(distances, axis=1)))
                    next_centers[cluster] = features[farthest_index]
                else:
                    next_centers[cluster] = np.mean(members, axis=0)
            if np.array_equal(next_labels, labels) and np.allclose(next_centers, centers, atol=1e-4):
                labels = next_labels
                centers = next_centers
                break
            labels = next_labels
            centers = next_centers

        inertia = float(np.sum((features - centers[labels]) ** 2))
        return labels, centers, inertia

    def _infer_num_clusters(
        self,
        scaled_features: np.ndarray,
        raw_features: np.ndarray,
        requested: Optional[int],
    ) -> int:
        if len(scaled_features) <= 1:
            return 1

        if requested is not None:
            return max(1, min(int(requested), len(scaled_features), 4))

        if len(scaled_features) < 8:
            return 1

        labels1, centers1, inertia1 = self._kmeans(scaled_features, 1)
        _ = labels1, centers1
        labels2, centers2, inertia2 = self._kmeans(scaled_features, 2)
        counts2 = np.bincount(labels2, minlength=2)
        smaller_ratio2 = float(np.min(counts2)) / float(max(1, len(labels2)))
        db_gap2 = abs(
            float(np.mean(raw_features[labels2 == 0, 0])) - float(np.mean(raw_features[labels2 == 1, 0]))
        )
        center_gap2 = float(np.linalg.norm(centers2[0] - centers2[1]))
        improvement12 = 1.0 - (inertia2 / max(inertia1, 1e-6))

        if smaller_ratio2 < 0.14 or (improvement12 < 0.16 and db_gap2 < 4.5 and center_gap2 < 0.9):
            return 1

        cluster_count = 2
        if len(scaled_features) < 18:
            return cluster_count

        labels3, centers3, inertia3 = self._kmeans(scaled_features, 3)
        counts3 = np.bincount(labels3, minlength=3)
        smaller_ratio3 = float(np.min(counts3)) / float(max(1, len(labels3)))
        improvement23 = 1.0 - (inertia3 / max(inertia2, 1e-6))
        center_gap3 = max(
            float(np.linalg.norm(centers3[a] - centers3[b]))
            for a in range(3)
            for b in range(a + 1, 3)
        )

        if smaller_ratio3 >= 0.10 and improvement23 > 0.10 and center_gap3 > 0.95:
            cluster_count = 3

        return cluster_count

    def _smooth_labels(self, labels: np.ndarray, intervals: list[dict]) -> np.ndarray:
        if len(labels) < 3:
            return labels

        smoothed = labels.copy()
        for _ in range(2):
            changed = False
            for index in range(1, len(smoothed) - 1):
                if smoothed[index - 1] != smoothed[index + 1] or smoothed[index] == smoothed[index - 1]:
                    continue
                gap_before = float(intervals[index]["start"]) - float(intervals[index - 1]["end"])
                gap_after = float(intervals[index + 1]["start"]) - float(intervals[index]["end"])
                duration = float(intervals[index]["duration"])
                if gap_before <= 0.45 and gap_after <= 0.45 and duration <= 1.25:
                    smoothed[index] = smoothed[index - 1]
                    changed = True
            if not changed:
                break
        return smoothed

    def _local_diarize(self, audio_path: str, num_speakers: Optional[int] = None) -> list[dict]:
        try:
            import librosa
        except ImportError as e:
            raise ImportError("Local speaker separation requires librosa to be installed") from e

        waveform, sr = self._load_waveform(audio_path, sample_rate=16000)
        if waveform.size == 0:
            return []

        hop_seconds = 0.02
        hop_length, frame_length, n_fft = _stft_window_params(sr, hop_seconds)

        spectrum = np.abs(
            librosa.stft(
                waveform,
                n_fft=n_fft,
                hop_length=hop_length,
                win_length=frame_length,
                center=True,
            )
        )
        if spectrum.size == 0:
            return []

        rms = librosa.feature.rms(S=spectrum, frame_length=n_fft)[0]
        centroid = librosa.feature.spectral_centroid(S=spectrum, sr=sr)[0]
        rolloff = librosa.feature.spectral_rolloff(S=spectrum, sr=sr)[0]
        flatness = librosa.feature.spectral_flatness(S=np.maximum(spectrum, 1e-10))[0]
        if rms.size == 0:
            return []

        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        power = spectrum ** 2
        total_power = np.mean(power, axis=0) + 1e-9
        speech_band_mask = (freqs >= 120.0) & (freqs <= 4500.0)
        high_band_mask = (freqs >= 3000.0) & (freqs <= min(float(sr) / 2.0, 9000.0))
        speech_band_power = np.mean(power[speech_band_mask], axis=0) if np.any(speech_band_mask) else total_power
        high_band_power = np.mean(power[high_band_mask], axis=0) if np.any(high_band_mask) else total_power
        speech_band_ratio = speech_band_power / total_power
        high_band_ratio = high_band_power / total_power
        times = librosa.frames_to_time(
            np.arange(spectrum.shape[1]),
            sr=sr,
            hop_length=hop_length,
            n_fft=n_fft,
        )

        def _norm(values: np.ndarray) -> np.ndarray:
            low = float(np.percentile(values, 10))
            high = float(np.percentile(values, 90))
            if high - low < 1e-9:
                return np.zeros_like(values, dtype=np.float32)
            return np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)

        speech_score = (
            0.32 * _norm(rms)
            + 0.22 * _norm(speech_band_ratio)
            + 0.16 * _norm(high_band_ratio)
            + 0.15 * _norm(rolloff)
            + 0.15 * (1.0 - _norm(flatness))
        )
        smooth_frames = max(3, int(round(0.08 / hop_seconds)))
        kernel = np.ones(smooth_frames, dtype=np.float32) / float(smooth_frames)
        speech_score = np.convolve(speech_score, kernel, mode="same")

        dbfs = 20.0 * np.log10(np.maximum(rms, 1e-6))
        speech_threshold = float(np.percentile(speech_score, 58))
        floor_db = max(-58.0, float(np.percentile(dbfs, 18)) - 4.0)
        active_mask = (speech_score >= speech_threshold) & (dbfs >= floor_db)

        intervals = self._extract_intervals(
            active_mask,
            times,
            hop_seconds=hop_seconds,
            min_frames=max(5, int(round(0.12 / hop_seconds))),
            merge_gap_frames=max(1, int(round(0.22 / hop_seconds))),
        )
        if not intervals:
            return []

        features = []
        valid_intervals = []
        for interval in intervals:
            start = float(interval["start"])
            end = float(interval["end"])
            frame_mask = (times >= max(0.0, start - hop_seconds)) & (times <= end + hop_seconds)
            frame_indices = np.where(frame_mask)[0]
            if frame_indices.size == 0:
                midpoint = (start + end) / 2.0
                frame_indices = np.array([int(np.argmin(np.abs(times - midpoint)))])

            avg_db = float(np.percentile(dbfs[frame_indices], 65))
            centroid_mean = float(np.mean(centroid[frame_indices]))
            rolloff_mean = float(np.mean(rolloff[frame_indices]))
            high_ratio_mean = float(np.mean(high_band_ratio[frame_indices]))
            flatness_mean = float(np.mean(flatness[frame_indices]))

            features.append([
                avg_db,
                centroid_mean,
                rolloff_mean,
                high_ratio_mean,
                flatness_mean,
            ])
            valid_intervals.append(interval)

        if not valid_intervals:
            return []

        raw_features = np.asarray(features, dtype=np.float32)
        scaled_features = self._robust_scale(raw_features)
        cluster_count = self._infer_num_clusters(scaled_features, raw_features, num_speakers)
        labels, _, _ = self._kmeans(scaled_features, cluster_count)
        labels = self._smooth_labels(labels, valid_intervals)

        label_order = sorted(
            range(cluster_count),
            key=lambda label: (
                -float(np.mean(raw_features[labels == label, 0])) if np.any(labels == label) else 0.0,
                -float(np.sum([valid_intervals[index]["duration"] for index in range(len(valid_intervals)) if labels[index] == label])),
            ),
        )
        label_map = {label: index for index, label in enumerate(label_order)}

        assigned_segments = []
        for interval, label in zip(valid_intervals, labels):
            mapped_index = label_map[int(label)]
            assigned_segments.append({
                "speaker": f"SPEAKER_{mapped_index + 1:02d}",
                "start": float(interval["start"]),
                "end": float(interval["end"]),
            })

        merged = [assigned_segments[0]]
        for segment in assigned_segments[1:]:
            previous = merged[-1]
            if segment["speaker"] == previous["speaker"] and segment["start"] - previous["end"] <= 0.35:
                previous["end"] = max(previous["end"], segment["end"])
            else:
                merged.append(segment)

        formatted = self._format_segments(merged)
        logger.info(
            "Local speaker separation complete: %d segments, %d speakers",
            len(formatted),
            len({seg["speaker"] for seg in formatted}),
        )
        return formatted

    def _pyannote_diarize(self, audio_path: str, num_speakers: Optional[int] = None) -> list[dict]:
        self._load_pipeline()

        kwargs = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers

        diarization_input = audio_path
        try:
            import torch

            waveform, sample_rate = self._load_waveform(audio_path, sample_rate=16000)
            if waveform.size > 0:
                diarization_input = {
                    "waveform": torch.from_numpy(waveform).unsqueeze(0),
                    "sample_rate": sample_rate,
                }
        except Exception as e:
            logger.warning("Preloaded waveform fallback unavailable, using file path: %s", e)

        diarization = self._pipeline(diarization_input, **kwargs)

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({
                "speaker": speaker,
                "start": round(turn.start, 3),
                "end": round(turn.end, 3),
                "duration": round(turn.end - turn.start, 3),
            })

        return self._format_segments(segments)

    def diarize_segments(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
    ) -> tuple[list[dict], str, str | None]:
        try:
            segments = self._pyannote_diarize(audio_path, num_speakers)
            return segments, "pyannote", None
        except Exception as e:
            logger.warning("Falling back to local speaker separation: %s", e)
            segments = self._local_diarize(audio_path, num_speakers)
            return segments, "local_fallback", str(e)

    def diarize(self, audio_path: str, num_speakers: Optional[int] = None) -> list[dict]:
        segments, _, _ = self.diarize_segments(audio_path, num_speakers)
        return segments

    async def diarize_with_progress(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
        progress_fn: Optional[Callable[[float, str], Awaitable]] = None,
    ) -> tuple[list[dict], str, str | None]:
        """Run diarization in a background thread with progress updates."""
        result_holder: list[tuple[list[dict], str, str | None] | None] = [None]
        error_holder = [None]
        done_event = threading.Event()

        def run_diarize():
            try:
                result_holder[0] = self.diarize_segments(audio_path, num_speakers)
            except Exception as e:
                error_holder[0] = e
            finally:
                done_event.set()

        if progress_fn:
            await progress_fn(0.05, "Loading speaker analysis...")

        thread = threading.Thread(target=run_diarize, daemon=True)
        start_time = time.time()
        thread.start()

        while not done_event.is_set():
            elapsed = time.time() - start_time
            pct = min(0.05 + elapsed / 120.0 * 0.85, 0.9)
            if progress_fn:
                await progress_fn(pct, f"Identifying speakers... ({int(elapsed)}s)")
            await asyncio.sleep(1.0)

        thread.join()

        if error_holder[0] is not None:
            raise error_holder[0]

        segments, method, reason = result_holder[0] or ([], "local_fallback", None)
        if progress_fn:
            speakers = list(dict.fromkeys(seg["speaker"] for seg in segments))
            label = "local speaker separation" if method == "local_fallback" else "speaker model"
            await progress_fn(1.0, f"Found {len(speakers)} speakers using {label}")

        return segments, method, reason

    def _speaker_scores_for_interval(
        self,
        start: float,
        end: float,
        diarization_segments: list[dict],
        edge_pad: float = 0.06,
    ) -> dict[str, float]:
        if not diarization_segments:
            return {}

        interval_start = max(0.0, float(start) - edge_pad)
        interval_end = max(interval_start, float(end) + edge_pad)
        midpoint = (interval_start + interval_end) / 2.0
        scores: dict[str, float] = {}

        for seg in diarization_segments:
            seg_start = float(seg["start"])
            seg_end = float(seg["end"])
            overlap_start = max(interval_start, seg_start)
            overlap_end = min(interval_end, seg_end)
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap <= 0.0:
                continue

            seg_span = max(0.12, seg_end - seg_start)
            seg_midpoint = (seg_start + seg_end) / 2.0
            proximity = max(0.0, 1.0 - abs(seg_midpoint - midpoint) / (seg_span + 0.18))
            speaker = str(seg["speaker"])
            scores[speaker] = scores.get(speaker, 0.0) + overlap * (1.0 + 0.18 * proximity)

        if scores:
            return scores

        nearest_segment = None
        nearest_distance = float("inf")
        for seg in diarization_segments:
            seg_start = float(seg["start"])
            seg_end = float(seg["end"])
            if interval_end < seg_start:
                distance = seg_start - interval_end
            elif interval_start > seg_end:
                distance = interval_start - seg_end
            else:
                distance = 0.0

            if distance < nearest_distance:
                nearest_distance = distance
                nearest_segment = seg

        if nearest_segment is not None:
            speaker = str(nearest_segment["speaker"])
            scores[speaker] = 0.05 / max(0.05, nearest_distance + 0.05)

        return scores

    def _build_word_turns(
        self,
        words: list[dict],
        provisional_speakers: list[str | None],
    ) -> list[list[int]]:
        if not words:
            return []

        turns: list[list[int]] = [[0]]
        for index in range(1, len(words)):
            current = words[index]
            previous = words[index - 1]
            gap = max(0.0, float(current["start"]) - float(previous["end"]))
            previous_text = str(previous.get("word", "")).strip()
            sentence_boundary = previous_text.endswith((".", "!", "?", ":", ";"))
            speaker_change = (
                provisional_speakers[index] is not None
                and provisional_speakers[index - 1] is not None
                and provisional_speakers[index] != provisional_speakers[index - 1]
            )

            if gap >= 0.45 or (gap >= 0.18 and (sentence_boundary or speaker_change)):
                turns.append([index])
            else:
                turns[-1].append(index)

        return turns

    def _collapse_short_speaker_islands(
        self,
        labels: list[str | None],
        words: list[dict],
    ) -> list[str | None]:
        if len(labels) < 3:
            return labels

        runs: list[dict] = []
        run_start = 0
        for index in range(1, len(labels) + 1):
            if index == len(labels) or labels[index] != labels[run_start]:
                runs.append({
                    "speaker": labels[run_start],
                    "start_index": run_start,
                    "end_index": index - 1,
                    "duration": max(
                        0.0,
                        float(words[index - 1]["end"]) - float(words[run_start]["start"]),
                    ),
                })
                run_start = index

        smoothed = labels[:]
        for index in range(1, len(runs) - 1):
            current = runs[index]
            previous = runs[index - 1]
            following = runs[index + 1]

            if previous["speaker"] != following["speaker"]:
                continue
            if current["speaker"] == previous["speaker"]:
                continue

            word_count = int(current["end_index"]) - int(current["start_index"]) + 1
            if word_count <= 2 or float(current["duration"]) <= 0.65:
                for word_index in range(int(current["start_index"]), int(current["end_index"]) + 1):
                    smoothed[word_index] = previous["speaker"]

        return smoothed

    def assign_speakers_to_words(
        self,
        words: list[dict],
        diarization_segments: list[dict],
    ) -> list[dict]:
        """
        Assign speaker labels to transcript words based on diarization segments.
        Uses the diarization segment with maximum overlap for each word, then
        falls back to the nearest speaker segment when no overlap exists.
        """
        if not diarization_segments:
            for word in words:
                word["speaker"] = None
                word["speaker_index"] = None
            return words

        speaker_index_map = {
            str(seg["speaker"]): seg.get("speaker_index")
            for seg in diarization_segments
            if seg.get("speaker") is not None
        }

        score_maps: list[dict[str, float]] = []
        provisional_speakers: list[str | None] = []
        for word in words:
            word_start = float(word["start"])
            word_end = float(word["end"])
            scores = self._speaker_scores_for_interval(word_start, word_end, diarization_segments)
            score_maps.append(scores)
            provisional_speakers.append(max(scores, key=scores.get) if scores else None)

        smoothed_speakers = provisional_speakers[:]
        for turn in self._build_word_turns(words, provisional_speakers):
            aggregated_scores: dict[str, float] = {}
            turn_duration = 0.0
            for word_index in turn:
                word = words[word_index]
                word_duration = max(0.05, float(word["end"]) - float(word["start"]))
                turn_duration += word_duration
                for speaker, score in score_maps[word_index].items():
                    aggregated_scores[speaker] = aggregated_scores.get(speaker, 0.0) + score * (0.6 + word_duration)

            if not aggregated_scores:
                continue

            ranked = sorted(aggregated_scores.items(), key=lambda item: item[1], reverse=True)
            dominant_speaker, dominant_score = ranked[0]
            runner_up_score = ranked[1][1] if len(ranked) > 1 else 0.0
            total_score = sum(aggregated_scores.values()) or dominant_score
            dominance = dominant_score / max(total_score, 1e-6)
            margin_ratio = dominant_score / max(runner_up_score, 1e-6) if runner_up_score > 0 else float("inf")

            if dominance >= 0.52 or margin_ratio >= 1.15 or turn_duration <= 1.8 or len(turn) <= 3:
                for word_index in turn:
                    smoothed_speakers[word_index] = dominant_speaker

        smoothed_speakers = self._collapse_short_speaker_islands(smoothed_speakers, words)

        for word, speaker in zip(words, smoothed_speakers):
            if speaker is not None:
                word["speaker"] = speaker
                speaker_index = speaker_index_map.get(str(speaker))
                word["speaker_index"] = int(speaker_index) if speaker_index is not None else None
            else:
                word["speaker"] = None
                word["speaker_index"] = None

        return words


diarization_service = DiarizationService()
