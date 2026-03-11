"""
Audio analysis service: WhisperX transcription, silence detection, filler word detection.
"""

import os
import json
import hashlib
import asyncio
import logging
import re
import time
import threading
from pathlib import Path
from typing import Optional, Callable, Awaitable, Any
import numpy as np
from config import settings
from utils.audio_utils import get_audio_duration, load_audio_np

logger = logging.getLogger("finlaycut.audio_service")


def _stft_window_params(sr: int, hop_ms: int, minimum_n_fft: int = 1024) -> tuple[int, int, int]:
    hop_length = max(1, int(sr * (hop_ms / 1000.0)))
    win_length = max(512, hop_length * 4)
    n_fft = max(int(minimum_n_fft), 1)
    while n_fft < win_length:
        n_fft *= 2
    return hop_length, win_length, n_fft


class AudioService:
    def __init__(self):
        self._whisper_model = None
        self._align_model = None
        self._align_metadata = None
        self._model_name = settings.whisper_model
        self._device = settings.whisper_device
        self._compute_type = settings.whisper_compute_type
        self._transcript_cache_version = "v9_filler_offset_fix"

    def _get_cache_key(self, audio_path: str, language: str | None = None) -> str:
        """Generate a cache key based on file path and modification time."""
        stat = os.stat(audio_path)
        raw = (
            f"{audio_path}:{stat.st_size}:{stat.st_mtime}:"
            f"{self._model_name}:{language or 'auto'}:{self._transcript_cache_version}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _get_cached_transcript(self, cache_key: str) -> dict | None:
        """Check if we have a cached transcript."""
        cache_file = settings.cache_dir / f"transcript_{cache_key}.json"
        if cache_file.exists():
            with open(cache_file, "r") as f:
                return json.load(f)
        return None

    def _save_transcript_cache(self, cache_key: str, transcript: dict):
        """Save transcript to cache."""
        cache_file = settings.cache_dir / f"transcript_{cache_key}.json"
        with open(cache_file, "w") as f:
            json.dump(transcript, f)

    def _speed_factors_path(self) -> Path:
        return settings.cache_dir / "speed_factors.json"

    def _load_speed_factor(self, model_name: str) -> float | None:
        """Load a previously saved transcription speed factor for a model."""
        path = self._speed_factors_path()
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return data.get(model_name)
        except Exception:
            return None

    def _save_speed_factor(self, model_name: str, speed: float):
        """Save/update the transcription speed factor for a model."""
        path = self._speed_factors_path()
        data = {}
        if path.exists():
            try:
                with open(path, "r") as f:
                    data = json.load(f)
            except Exception:
                pass
        data[model_name] = round(speed, 3)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    def load_whisper_model(self, model_name: str | None = None):
        """Load or reload the WhisperX model."""
        name = model_name or self._model_name
        if self._whisper_model is not None and self._model_name == name:
            return

        import whisperx

        # Prompt that conditions the model to transcribe filler words verbatim
        filler_prompt = (
            "Um, uh, uhm, umm, hmm, mm, erm, ah. Keep hesitation sounds exactly as spoken. "
            "Do not omit filler words at the beginning of sentences. "
            "Transcribe brief fillers such as um, uh, erm, ah, hmm, mm, mhm verbatim, including after pauses."
        )

        logger.info("Loading WhisperX model: %s (device=%s, compute=%s)", name, self._device, self._compute_type)
        asr_opts = {
            "initial_prompt": filler_prompt,
            "condition_on_previous_text": False,  # Re-apply prompt every segment
            "no_speech_threshold": 0.82,
            "suppress_blank": False,
        }
        try:
            # hotwords boosts probability of filler words (requires faster-whisper >= 1.0)
            self._whisper_model = whisperx.load_model(
                name,
                device=self._device,
                compute_type=self._compute_type,
                asr_options={
                    **asr_opts,
                    "hotwords": (
                        "um uh uhm umm uhh uhhh uh-huh erm er ah ahh hmm hm mm mhm mmhm mmhmm"
                    ),
                },
            )
        except TypeError:
            # Older whisperx/faster-whisper without hotwords support
            logger.info("hotwords not supported, falling back to initial_prompt only")
            self._whisper_model = whisperx.load_model(
                name,
                device=self._device,
                compute_type=self._compute_type,
                asr_options=asr_opts,
            )
        self._model_name = name
        self._transcript_cache_version = "v9_filler_offset_fix"  # Bust old caches
        logger.info("WhisperX model loaded: %s (filler word detection enabled)", name)

    @staticmethod
    def _clean_filler_token(text: str) -> str:
        return re.sub(r"[^a-z-]", "", text.lower())

    @classmethod
    def _is_hesitation_token(cls, text: str) -> bool:
        cleaned = cls._clean_filler_token(text)
        if not cleaned:
            return False
        if cleaned in {
            "um", "uh", "uh-huh", "uhh", "uhm", "umm",
            "hmm", "hm", "mhm", "mmhm", "mmhmm", "mm", "mmm",
            "huh", "er", "erm", "ah", "ahh",
        }:
            return True
        return any(
            pattern.match(cleaned)
            for pattern in (
                re.compile(r"^u+h+$"),
                re.compile(r"^u+m+h*$"),
                re.compile(r"^u+h+m*$"),
                re.compile(r"^u+m+$"),
                re.compile(r"^[hm]+$"),
                re.compile(r"^m+h+m+$"),
                re.compile(r"^e+r+m*$"),
                re.compile(r"^a+h+$"),
            )
        )

    @classmethod
    def _hesitation_family(cls, text: str) -> str | None:
        cleaned = cls._clean_filler_token(text)
        if not cleaned:
            return None
        if cleaned == "uh-huh":
            return "uh"
        if cleaned in {"er", "erm"} or re.fullmatch(r"e+r+m*", cleaned):
            return "erm"
        if cleaned in {"ah", "ahh"} or re.fullmatch(r"a+h+", cleaned):
            return "ah"
        if cleaned in {"um", "umm", "uhm"} or re.fullmatch(r"u+m+h*", cleaned) or re.fullmatch(r"u+h+m*", cleaned) or re.fullmatch(r"u+m+", cleaned):
            return "um"
        if cleaned in {"uh", "uhh", "huh"} or re.fullmatch(r"u+h+", cleaned):
            return "uh"
        if cleaned in {"hmm", "hm", "mhm", "mmhm", "mmhmm", "mm", "mmm"} or re.fullmatch(r"^[hm]+$", cleaned) or re.fullmatch(r"m+h+m+", cleaned):
            return "hm"
        return None

    @classmethod
    def _allowed_hesitation_families(cls, filler_words: list[str] | None = None) -> set[str]:
        source = filler_words if filler_words is not None else settings.filler_words
        families: set[str] = set()
        for filler in source:
            if " " in filler:
                continue
            family = cls._hesitation_family(filler)
            if family:
                families.add(family)
        return families

    @staticmethod
    def _patterns_for_hesitation_families(families: set[str]) -> list[re.Pattern[str]]:
        patterns: list[re.Pattern[str]] = []
        if "uh" in families:
            patterns.append(re.compile(r"^u+h+$"))
        if "um" in families:
            patterns.extend([
                re.compile(r"^u+m+h*$"),
                re.compile(r"^u+h+m*$"),
                re.compile(r"^u+m+$"),
            ])
        if "hm" in families:
            patterns.extend([
                re.compile(r"^[hm]+$"),
                re.compile(r"^m+h+m+$"),
            ])
        if "erm" in families:
            patterns.append(re.compile(r"^e+r+m*$"))
        if "ah" in families:
            patterns.append(re.compile(r"^a+h+$"))
        return patterns

    @staticmethod
    def _estimate_audio_hesitation_label(
        allowed_families: set[str],
        duration: float,
        avg_centroid: float,
        avg_rolloff: float,
        avg_zcr: float,
        avg_high_band: float,
    ) -> str:
        ordered_families = [family for family in ("um", "uh", "erm", "ah", "hm") if family in allowed_families]
        if not ordered_families:
            return "[filler]"
        if len(ordered_families) == 1:
            return ordered_families[0]

        if "um" in allowed_families and "uh" in allowed_families:
            nasal_score = 0
            if duration >= 0.18:
                nasal_score += 1
            if avg_centroid <= 1350.0:
                nasal_score += 1
            if avg_rolloff <= 2200.0:
                nasal_score += 1
            if avg_high_band <= 0.11:
                nasal_score += 1
            if avg_zcr <= 0.09:
                nasal_score += 1
            return "um" if nasal_score >= 3 else "uh"

        return ordered_families[0]

    @classmethod
    def _is_repetition_or_stutter_context(
        cls,
        prev_word: dict | None,
        next_word: dict | None,
    ) -> bool:
        prev_token = cls._clean_filler_token(prev_word.get("word", "")) if isinstance(prev_word, dict) else ""
        next_token = cls._clean_filler_token(next_word.get("word", "")) if isinstance(next_word, dict) else ""
        if not prev_token or not next_token:
            return False
        common_short_words = {
            "an", "as", "at", "be", "by", "do", "go", "he", "if",
            "in", "is", "it", "me", "my", "no", "of", "on", "or",
            "so", "to", "up", "us", "we",
        }
        if prev_token == next_token:
            if prev_token in common_short_words:
                return False
            return True

        shorter, longer = sorted((prev_token, next_token), key=len)
        if not longer.startswith(shorter):
            return False

        if len(shorter) >= 3 and len(longer) - len(shorter) <= 4:
            return True

        return (
            len(shorter) == 2
            and shorter not in common_short_words
            and shorter[0] not in {"a", "e", "i", "o", "u"}
            and len(longer) >= 5
            and len(longer) - len(shorter) <= 6
        )

    @staticmethod
    def _matches_confident_hesitation_family(
        label: str,
        duration: float,
        avg_centroid: float,
        avg_rolloff: float,
        avg_zcr: float,
        avg_high_band: float,
        avg_flatness: float,
    ) -> bool:
        if label == "um":
            return (
                0.09 <= duration <= 0.34
                and avg_centroid <= 1550.0
                and avg_rolloff <= 2550.0
                and avg_zcr <= 0.14
                and avg_high_band <= 0.15
                and avg_flatness <= 0.34
            )
        if label == "uh":
            return (
                0.08 <= duration <= 0.28
                and avg_centroid <= 1750.0
                and avg_rolloff <= 3000.0
                and avg_zcr <= 0.15
                and avg_high_band <= 0.18
                and avg_flatness <= 0.34
            )
        return False

    @staticmethod
    def _estimate_word_relative_frame(
        word: dict,
        target_time: float,
        frame_key_start: str,
        frame_key_end: str,
    ) -> int | None:
        start_frame = word.get(frame_key_start)
        end_frame = word.get(frame_key_end)
        start_time = word.get("start")
        end_time = word.get("end")
        if start_frame is None or end_frame is None or start_time is None or end_time is None:
            return None
        try:
            start_frame = int(start_frame)
            end_frame = int(end_frame)
            start_time = float(start_time)
            end_time = float(end_time)
            target_time = float(target_time)
        except Exception:
            return None

        if target_time <= start_time:
            return start_frame
        if target_time >= end_time:
            return end_frame
        if end_time <= start_time:
            return start_frame

        ratio = max(0.0, min(1.0, (target_time - start_time) / max(end_time - start_time, 1e-6)))
        return int(round(start_frame + ((end_frame - start_frame) * ratio)))

    def _load_align_model(self, language_code: str):
        """Load the alignment model for a specific language."""
        import whisperx

        logger.info("Loading alignment model for language: %s", language_code)
        self._align_model, self._align_metadata = whisperx.load_align_model(
            language_code=language_code,
            device=self._device,
        )

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        use_cache: bool = True,
    ) -> dict:
        """
        Transcribe audio using WhisperX with word-level timestamps via forced alignment.
        This is a blocking call — use transcribe_with_progress() for async progress.
        """
        import whisperx

        # Check cache
        if use_cache:
            cache_key = self._get_cache_key(audio_path, language)
            cached = self._get_cached_transcript(cache_key)
            if cached is not None:
                logger.info("Using cached transcript for %s", audio_path)
                return cached

        self.load_whisper_model()

        # Step 1: Transcribe with faster-whisper (VAD + batched inference)
        # Use our own load_audio_np which resolves the full ffmpeg path
        # (whisperx.load_audio calls bare 'ffmpeg' which fails inside Electron)
        audio = load_audio_np(audio_path)
        # batch_size=16 is only safe on GPU; on CPU it pegs all cores and can
        # cause thermal shutdowns.  Use a conservative batch size for CPU.
        batch = 4 if self._device == "cpu" else 16
        result = self._whisper_model.transcribe(audio, batch_size=batch, language=language)

        detected_language = result.get("language", language or "en")

        # Step 2: Forced alignment for precise word timestamps
        alignment_method = settings.alignment_method
        if alignment_method == "mms_fa":
            try:
                from services.alignment_service import get_alignment_service
                align_svc = get_alignment_service()
                # Convert result segments into alignment-friendly format
                pre_segments = self._convert_result(result, detected_language)
                aligned_segments = align_svc.align_segments(
                    audio_path, pre_segments["segments"],
                )
                pre_segments["segments"] = aligned_segments
                # Flatten words from aligned segments
                pre_segments["words"] = []
                for seg in aligned_segments:
                    pre_segments["words"].extend(seg.get("words", []))
                # Waveform refinement
                if settings.alignment_waveform_refine:
                    pre_segments = self._refine_word_boundaries(audio_path, pre_segments)
                transcript = pre_segments
                logger.info("MMS_FA forced alignment complete")
            except Exception as e:
                logger.warning("MMS_FA alignment failed (%s), falling back to whisperx", e)
                whisperx_ok = False
                try:
                    self._load_align_model(detected_language)
                    result = whisperx.align(
                        result["segments"],
                        self._align_model,
                        self._align_metadata,
                        audio,
                        self._device,
                        return_char_alignments=False,
                    )
                    logger.info("WhisperX fallback alignment complete")
                    whisperx_ok = True
                except Exception as e2:
                    logger.warning("WhisperX alignment also failed (%s), using raw timestamps", e2)
                transcript = self._convert_result(result, detected_language)
                self._mark_words_alignment_method(
                    transcript, "whisperx" if whisperx_ok else "fallback"
                )
        else:
            whisperx_ok = False
            try:
                self._load_align_model(detected_language)
                result = whisperx.align(
                    result["segments"],
                    self._align_model,
                    self._align_metadata,
                    audio,
                    self._device,
                    return_char_alignments=False,
                )
                logger.info("WhisperX forced alignment complete")
                whisperx_ok = True
            except Exception as e:
                logger.warning("Forced alignment failed (%s), using raw timestamps", e)
            transcript = self._convert_result(result, detected_language)
            self._mark_words_alignment_method(
                transcript, "whisperx" if whisperx_ok else "fallback"
            )

        # Cache the result
        if use_cache:
            self._save_transcript_cache(cache_key, transcript)

        return transcript

    async def transcribe_with_progress(
        self,
        audio_path: str,
        language: str | None = None,
        use_cache: bool = True,
        progress_fn: Callable[[float, str], Awaitable] | None = None,
    ) -> dict:
        """
        Transcribe with real progress updates.
        Runs WhisperX in a thread while sending estimated progress via WebSocket.
        """
        # Check cache first (instant)
        if use_cache:
            cache_key = self._get_cache_key(audio_path, language)
            cached = self._get_cached_transcript(cache_key)
            if cached is not None:
                logger.info("Using cached transcript")
                if progress_fn:
                    await progress_fn(1.0, "Using cached transcript")
                return cached

        # Get audio duration to estimate progress
        try:
            audio_duration = get_audio_duration(audio_path)
        except Exception:
            audio_duration = 60.0  # fallback estimate

        # Check for saved speed factor from previous runs
        saved_speed = self._load_speed_factor(self._model_name)
        if saved_speed is not None:
            speed_factor = saved_speed
            has_real_estimate = True
        else:
            # Fallback: rough guesses for first-time runs
            model_speeds = {
                "tiny": 20.0, "base": 12.0, "small": 6.0,
                "medium": 3.0, "large": 1.5, "large-v2": 1.5, "large-v3": 1.5,
            }
            speed_factor = model_speeds.get(self._model_name, 3.0)
            has_real_estimate = False
        estimated_seconds = audio_duration / speed_factor

        if progress_fn:
            await progress_fn(0.05, "Loading WhisperX model...")

        self.load_whisper_model()

        if progress_fn:
            await progress_fn(0.1, "Transcribing with WhisperX...")

        # Run the blocking transcription in a thread
        result_holder = [None]
        error_holder = [None]
        done_event = threading.Event()

        def run_transcribe():
            try:
                import whisperx

                # Step 1: Transcribe
                # Use our own load_audio_np which resolves the full ffmpeg path
                audio = load_audio_np(audio_path)
                batch = 4 if self._device == "cpu" else 16
                result = self._whisper_model.transcribe(audio, batch_size=batch, language=language)

                detected_language = result.get("language", language or "en")

                # Step 2: Forced alignment (MMS_FA or WhisperX)
                alignment_method = settings.alignment_method
                used_mms_fa = False
                if alignment_method == "mms_fa":
                    try:
                        from services.alignment_service import get_alignment_service
                        align_svc = get_alignment_service()
                        # Convert to intermediate format for alignment
                        pre_transcript = self._convert_result(result, detected_language)
                        aligned_segments = align_svc.align_segments(
                            audio_path, pre_transcript["segments"],
                        )
                        pre_transcript["segments"] = aligned_segments
                        pre_transcript["words"] = []
                        for seg in aligned_segments:
                            pre_transcript["words"].extend(seg.get("words", []))
                        # Waveform refinement
                        if settings.alignment_waveform_refine:
                            pre_transcript = self._refine_word_boundaries(audio_path, pre_transcript)
                        result_holder[0] = (pre_transcript, detected_language, True, "mms_fa")
                        used_mms_fa = True
                        logger.info("MMS_FA forced alignment complete")
                    except Exception as e:
                        logger.warning("MMS_FA alignment failed (%s), falling back to whisperx", e)

                if not used_mms_fa:
                    whisperx_ok = False
                    try:
                        self._load_align_model(detected_language)
                        result = whisperx.align(
                            result["segments"],
                            self._align_model,
                            self._align_metadata,
                            audio,
                            self._device,
                            return_char_alignments=False,
                        )
                        logger.info("WhisperX forced alignment complete")
                        whisperx_ok = True
                    except Exception as e:
                        logger.warning("Forced alignment failed (%s), using raw timestamps", e)
                    fallback_method = "whisperx" if whisperx_ok else "fallback"
                    result_holder[0] = (result, detected_language, False, fallback_method)
            except Exception as e:
                error_holder[0] = e
            finally:
                done_event.set()

        thread = threading.Thread(target=run_transcribe, daemon=True)
        start_time = time.time()
        thread.start()

        # Send progress while transcription runs
        while not done_event.is_set():
            elapsed = time.time() - start_time

            if has_real_estimate:
                # We have a calibrated speed factor — send real time-based progress
                raw_pct = min(elapsed / max(estimated_seconds, 1.0), 0.95)
                pct = 0.1 + raw_pct * 0.75
            else:
                # No calibration data — send a fixed value so the frontend
                # knows we're working but doesn't show a fake ETA
                pct = 0.15

            if progress_fn:
                await progress_fn(pct, "Transcribing...")

            # Wait a bit before next update
            await asyncio.sleep(1.0)

        # Join with timeout to prevent infinite hang if thread is stuck.
        # Scale timeout with audio duration: at least 10 min, plus 2x the estimated time.
        join_timeout = max(600, estimated_seconds * 2 + 120)
        thread.join(timeout=join_timeout)
        if thread.is_alive():
            timeout_min = int(join_timeout // 60)
            logger.error("Transcription thread hung after %d minutes — aborting", timeout_min)
            raise RuntimeError(f"Transcription timed out after {timeout_min} minutes. The audio file may be too large or corrupt.")

        if error_holder[0] is not None:
            raise error_holder[0]

        if result_holder[0] is None:
            raise RuntimeError("Transcription produced no result. The audio may be empty or corrupt.")

        # Save actual speed factor for future estimates
        transcribe_elapsed = time.time() - start_time
        if transcribe_elapsed > 1.0 and audio_duration > 1.0:
            actual_speed = audio_duration / transcribe_elapsed
            self._save_speed_factor(self._model_name, actual_speed)
            logger.info("Saved speed factor for %s: %.2fx realtime", self._model_name, actual_speed)

        if progress_fn:
            await progress_fn(0.88, "Processing results...")

        result_or_transcript, detected_language, was_mms_fa, align_method = result_holder[0]

        if was_mms_fa:
            # Already converted + aligned inside the thread
            transcript = result_or_transcript
        else:
            transcript = self._convert_result(result_or_transcript, detected_language)
            self._mark_words_alignment_method(transcript, align_method)

        # Cache the result
        if use_cache:
            cache_key = self._get_cache_key(audio_path, language)
            self._save_transcript_cache(cache_key, transcript)

        if progress_fn:
            await progress_fn(0.95, "Transcription complete")

        return transcript

    def _coerce_timestamp(self, value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _normalize_segment_words(self, segment: dict) -> list[dict]:
        """Normalize WhisperX word timings into monotonic per-word ranges."""
        seg_start = self._coerce_timestamp(segment.get("start")) or 0.0
        seg_end = self._coerce_timestamp(segment.get("end")) or seg_start
        if seg_end < seg_start:
            seg_end = seg_start

        raw_words = segment.get("words", []) or []
        if not raw_words:
            fallback_words = [token.strip() for token in str(segment.get("text", "")).strip().split() if token.strip()]
            if not fallback_words:
                return []
            raw_words = [
                {
                    "word": token,
                    "score": 0.5,
                    "start": None,
                    "end": None,
                }
                for token in fallback_words
            ]

        normalized = []
        for word in raw_words:
            normalized.append({
                "word": word.get("word", "").strip(),
                "probability": word.get("score", 1.0),
                "raw_start": self._coerce_timestamp(word.get("start")),
                "raw_end": self._coerce_timestamp(word.get("end")),
            })

        if not any(w["raw_start"] is not None or w["raw_end"] is not None for w in normalized):
            span = max(0.0, seg_end - seg_start)
            step = span / max(len(normalized), 1)
            cursor = seg_start
            for word in normalized:
                word["start"] = cursor
                cursor = min(seg_end, cursor + step)
                word["end"] = cursor
            return normalized

        def _next_known_start(index: int) -> float | None:
            for probe in range(index + 1, len(normalized)):
                value = normalized[probe]["raw_start"]
                if value is not None:
                    return value
            return None

        cursor = seg_start
        for index, word in enumerate(normalized):
            start = word["raw_start"]
            if start is None:
                start = cursor
            start = max(seg_start, min(float(start), seg_end))
            start = max(cursor, start)

            end = word["raw_end"]
            if end is None:
                next_start = _next_known_start(index)
                end = next_start if next_start is not None else seg_end
            end = max(start, min(float(end), seg_end))

            next_start = _next_known_start(index)
            if next_start is not None:
                end = min(end, max(start, float(next_start)))

            if end <= start:
                remaining_words = max(1, len(normalized) - index)
                step = max(0.01, (seg_end - start) / remaining_words)
                end = min(seg_end, start + step)

            word["start"] = start
            word["end"] = max(start, end)
            cursor = word["end"]

        return normalized

    def _convert_result(self, result: dict, language: str | None = None) -> dict:
        """Convert WhisperX result dict to our internal format."""
        segments_raw = result.get("segments", [])

        transcript = {
            "text": " ".join(seg.get("text", "").strip() for seg in segments_raw),
            "language": language,
            "segments": [],
            "words": [],
        }

        for segment in segments_raw:
            seg_data = {
                "start": segment.get("start", 0),
                "end": segment.get("end", 0),
                "text": segment.get("text", "").strip(),
                "words": [],
            }

            for word in self._normalize_segment_words(segment):
                word_data = {
                    "word": word.get("word", "").strip(),
                    "start": word.get("start", seg_data["start"]),
                    "end": word.get("end", seg_data["end"]),
                    "probability": word.get("probability", 1.0),
                }
                seg_data["words"].append(word_data)
                transcript["words"].append(word_data)

            transcript["segments"].append(seg_data)

        return transcript

    @staticmethod
    def _mark_words_alignment_method(transcript: dict, method: str):
        """Tag all words in a transcript with an alignment method."""
        for word in transcript.get("words", []):
            if "alignment_method" not in word:
                word["alignment_method"] = method
        for seg in transcript.get("segments", []):
            for word in seg.get("words", []):
                if "alignment_method" not in word:
                    word["alignment_method"] = method

    def _refine_word_boundaries(self, audio_path: str, transcript: dict) -> dict:
        """Apply waveform onset/offset detection + zero-crossing snap to all words.

        Reuses the existing spectral profile and boundary refinement code
        (previously only used for filler words) to polish every word boundary.
        """
        words = transcript.get("words", [])
        if not words:
            return transcript

        profile = self.build_waveform_profile(audio_path, sample_rate=16000, hop_ms=5)
        if profile is None:
            return transcript

        try:
            y_raw = load_audio_np(audio_path, sr=16000)
        except Exception:
            y_raw = None

        times = profile["times"]
        rms = profile["rms"]
        duration = float(profile["duration"])
        hop_seconds = float(profile["hop_seconds"])

        # Smooth RMS for onset/offset detection
        smooth_k = max(3, int(round(0.015 / max(hop_seconds, 1e-6))))
        kernel = np.ones(smooth_k, dtype=np.float32) / float(smooth_k)
        rms_smooth = np.convolve(rms, kernel, mode="same").astype(np.float32)

        search_window_s = 0.180  # 180ms search window for word boundaries

        for i, word in enumerate(words):
            try:
                start = float(word.get("start", 0))
                end = float(word.get("end", 0))
            except (TypeError, ValueError):
                continue
            if end <= start or start < 0:
                continue

            # Neighbor boundaries for clamping
            prev_end = float(words[i - 1].get("end", 0)) if i > 0 else 0.0
            next_start = float(words[i + 1].get("start", duration)) if i < len(words) - 1 else duration

            # --- Onset refinement (search backward from word start) ---
            backward_limit = max(prev_end, start - search_window_s)
            onset_search = np.where((times >= backward_limit) & (times <= start))[0]
            new_start = start
            if onset_search.size >= 2:
                local_rms = rms_smooth[onset_search]
                noise_ref = float(np.percentile(local_rms, 30))
                onset_threshold = max(noise_ref * 2.5, noise_ref + 0.003)
                above = np.where(local_rms > onset_threshold)[0]
                if above.size > 0:
                    candidate = float(times[onset_search[above[0]]])
                    if candidate < start:
                        new_start = candidate

            # --- Offset refinement (search forward from word end) ---
            forward_limit = min(next_start, end + search_window_s)
            offset_search = np.where((times >= end) & (times <= forward_limit))[0]
            new_end = end
            if offset_search.size >= 2:
                local_rms = rms_smooth[offset_search]
                noise_ref = float(np.percentile(local_rms, 30))
                offset_threshold = max(noise_ref * 2.5, noise_ref + 0.003)
                above = np.where(local_rms > offset_threshold)[0]
                if above.size > 0:
                    candidate = float(times[offset_search[above[-1]]]) + hop_seconds
                    if candidate > end:
                        new_end = candidate

            # --- Zero-crossing snap ---
            if y_raw is not None and y_raw.size > 0:
                sr = 16000
                s_sample = max(0, min(len(y_raw) - 1, int(round(new_start * sr))))
                e_sample = max(0, min(len(y_raw) - 1, int(round(new_end * sr))))
                new_start = self._find_nearest_zero_crossing(y_raw, s_sample, sr=sr) / sr
                new_end = self._find_nearest_zero_crossing(y_raw, e_sample, sr=sr) / sr

            # --- Clamp to neighbors ---
            new_start = max(prev_end, max(0.0, new_start))
            new_end = min(next_start, min(duration, new_end))

            if new_end <= new_start:
                # Refinement squeezed the word to nothing, but still fix
                # any cross-segment overlap by clamping start to prev_end
                if start < prev_end:
                    word["start"] = round(prev_end, 4)
                    word["end"] = round(max(prev_end + 0.001, end), 4)
                continue

            word["start"] = round(new_start, 4)
            word["end"] = round(max(new_start + 0.001, new_end), 4)
            word["boundary_refined"] = True

        # Global overlap-fix pass — ensure monotonic timestamps across all words
        # (catches any cross-segment overlaps not handled by per-word clamping)
        for i in range(1, len(words)):
            prev_end = float(words[i - 1].get("end", 0))
            curr_start = float(words[i].get("start", 0))
            if curr_start < prev_end:
                mid = round((prev_end + curr_start) / 2.0, 4)
                words[i - 1]["end"] = mid
                words[i]["start"] = mid

        # Update segment boundaries to match refined words
        for seg in transcript.get("segments", []):
            seg_words = seg.get("words", [])
            if seg_words:
                seg["start"] = seg_words[0].get("start", seg.get("start"))
                seg["end"] = seg_words[-1].get("end", seg.get("end"))

        return transcript

    async def detect_silence_with_progress(
        self,
        audio_path: str,
        threshold_db: int = -40,
        min_duration_ms: int = 300,
        progress_fn: Callable[[float, str], Awaitable] | None = None,
    ) -> list[dict]:
        """
        Detect silent segments in audio, with progress updates.
        Runs the blocking pydub work in a thread.
        """
        result_holder = [None]
        error_holder = [None]
        done_event = threading.Event()

        def run_detect():
            try:
                from pydub import AudioSegment
                from pydub.silence import detect_silence as pydub_detect_silence
                from utils.audio_utils import get_ffmpeg_path
                AudioSegment.converter = get_ffmpeg_path()

                audio = AudioSegment.from_file(audio_path)
                silent_ranges = pydub_detect_silence(
                    audio,
                    min_silence_len=min_duration_ms,
                    silence_thresh=threshold_db
                )

                silences = []
                for start_ms, end_ms in silent_ranges:
                    start_s = start_ms / 1000.0
                    end_s = end_ms / 1000.0
                    silences.append({
                        "start": start_s,
                        "end": end_s,
                        "duration": end_s - start_s,
                        "selected": True
                    })
                result_holder[0] = silences
            except Exception as e:
                error_holder[0] = e
            finally:
                done_event.set()

        if progress_fn:
            await progress_fn(0.1, "Loading audio...")

        # Get audio duration for progress estimation
        try:
            audio_duration = get_audio_duration(audio_path)
        except Exception:
            audio_duration = 60.0

        # Silence detection is typically ~2-5x realtime
        estimated_seconds = audio_duration / 3.0

        thread = threading.Thread(target=run_detect, daemon=True)
        start_time = time.time()
        thread.start()

        if progress_fn:
            await progress_fn(0.2, "Detecting silence...")

        while not done_event.is_set():
            elapsed = time.time() - start_time
            raw_pct = min(elapsed / max(estimated_seconds, 1.0), 0.95)
            pct = 0.2 + raw_pct * 0.6

            if progress_fn:
                await progress_fn(pct, f"Detecting silence... ({int(elapsed)}s)")

            await asyncio.sleep(0.5)

        # Join with timeout to prevent infinite hang
        thread.join(timeout=600)
        if thread.is_alive():
            logger.error("Silence detection thread hung after 10 minutes — aborting")
            raise RuntimeError("Silence detection timed out after 10 minutes.")

        if error_holder[0] is not None:
            raise error_holder[0]

        if result_holder[0] is None:
            raise RuntimeError("Silence detection produced no result.")

        silences = result_holder[0]

        if progress_fn:
            await progress_fn(1.0, f"Found {len(silences)} silent segments")

        return silences

    def detect_silence(
        self,
        audio_path: str,
        threshold_db: int = -40,
        min_duration_ms: int = 300,
        progress_callback=None
    ) -> list[dict]:
        """Synchronous silence detection (kept for backward compat)."""
        from pydub import AudioSegment
        from pydub.silence import detect_silence as pydub_detect_silence
        from utils.audio_utils import get_ffmpeg_path
        AudioSegment.converter = get_ffmpeg_path()

        if progress_callback:
            progress_callback(0.1, "Loading audio...")

        audio = AudioSegment.from_file(audio_path)

        if progress_callback:
            progress_callback(0.3, "Detecting silence...")

        silent_ranges = pydub_detect_silence(
            audio,
            min_silence_len=min_duration_ms,
            silence_thresh=threshold_db
        )

        if progress_callback:
            progress_callback(0.9, "Processing results...")

        silences = []
        for start_ms, end_ms in silent_ranges:
            start_s = start_ms / 1000.0
            end_s = end_ms / 1000.0
            silences.append({
                "start": start_s,
                "end": end_s,
                "duration": end_s - start_s,
                "selected": True
            })

        if progress_callback:
            progress_callback(1.0, f"Found {len(silences)} silent segments")

        return silences

    def detect_filler_words(
        self,
        transcript: dict,
        filler_words: list[str] | None = None
    ) -> list[dict]:
        """Detect filler words in a transcript using pattern matching.

        Uses regex patterns to catch all variants (um, umm, ummm, uh, uhh, etc.)
        plus exact matching for word-based fillers (like, basically, actually...).
        No limit on detection count — returns every filler found.
        """
        custom_fillers = filler_words if filler_words is not None else settings.filler_words

        min_hesitation_frames = 3
        allowed_families = self._allowed_hesitation_families(custom_fillers)
        filler_patterns = self._patterns_for_hesitation_families(allowed_families)

        strong_fillers: set[str] = set()
        contextual_fillers: set[str] = set()

        # Custom single-word fillers that are not true hesitation tokens stay contextual.
        for f in custom_fillers:
            if " " not in f:
                cleaned = self._clean_filler_token(f)
                if not cleaned:
                    continue
                if self._hesitation_family(cleaned) in allowed_families:
                    strong_fillers.add(cleaned)
                else:
                    contextual_fillers.add(cleaned)

        # Multi-word filler phrases
        multi_fillers = [
            ["you", "know"],
            ["i", "mean"],
            ["kind", "of"],
            ["sort", "of"],
            ["you", "know", "what", "i", "mean"],
        ]
        # Add any custom multi-word fillers
        for f in custom_fillers:
            if " " in f:
                parts = f.lower().split()
                if parts not in multi_fillers:
                    multi_fillers.append(parts)

        def _clean(text: str) -> str:
            return self._clean_filler_token(text)

        def _frame_span(word_data: dict) -> int | None:
            start_frame = word_data.get("start_frame")
            end_frame = word_data.get("end_frame")
            if start_frame is None or end_frame is None:
                return None
            try:
                return max(0, int(end_frame) - int(start_frame))
            except Exception:
                return None

        def _is_short_hesitation(word_data: dict, prev_word: dict | None, next_word: dict | None) -> bool:
            start = float(word_data.get("start", 0.0))
            end = float(word_data.get("end", start))
            duration = max(0.0, end - start)
            probability = float(word_data.get("probability", 1.0) or 1.0)
            frame_span = _frame_span(word_data)
            gap_before = max(0.0, start - float(prev_word.get("end", start))) if prev_word else 0.0
            gap_after = max(0.0, float(next_word.get("start", end)) - end) if next_word else 0.0
            has_pause = gap_before >= 0.05 or gap_after >= 0.05
            is_short = duration <= 0.45 and (
                duration >= 0.08 or (frame_span is not None and frame_span >= min_hesitation_frames)
            )
            return is_short and has_pause and (probability < 0.9 or duration <= 0.2)

        detections = []
        words = transcript.get("words", [])
        detected_indices: set[int] = set()  # Track which word indices are already detected

        logger.debug(
            "detect_filler_words: scanning %d words, strong_fillers=%s",
            len(words),
            sorted(strong_fillers),
        )

        # Single-word detection
        for i, word_data in enumerate(words):
            cleaned = _clean(word_data.get("word", ""))
            if not cleaned:
                continue

            prev_word = words[i - 1] if i > 0 else None
            next_word = words[i + 1] if i < len(words) - 1 else None

            is_strong_filler = cleaned in strong_fillers or any(p.match(cleaned) for p in filler_patterns)
            is_contextual_filler = cleaned in contextual_fillers and _is_short_hesitation(word_data, prev_word, next_word)
            is_filler = is_strong_filler or is_contextual_filler
            if is_filler:
                detected_indices.add(i)
                context_start = max(0, i - 3)
                context_end = min(len(words), i + 4)
                context = " ".join(w["word"] for w in words[context_start:context_end])
                detections.append({
                    "word": word_data["word"],
                    "start": word_data["start"],
                    "end": word_data["end"],
                    "detection_source": "transcript_lexical",
                    "start_frame": word_data.get("start_frame"),
                    "end_frame": word_data.get("end_frame"),
                    "source_start_frame": word_data.get("source_start_frame"),
                    "source_end_frame": word_data.get("source_end_frame"),
                    "context": context,
                    "selected": True,
                    "_first_index": i,
                    "_last_index": i,
                })

        # Multi-word detection
        for phrase in multi_fillers:
            phrase_len = len(phrase)
            for i in range(len(words) - phrase_len + 1):
                # Skip if any word in the window is already detected
                if any((i + j) in detected_indices for j in range(phrase_len)):
                    continue
                window = [_clean(words[i + j].get("word", "")) for j in range(phrase_len)]
                if window == phrase:
                    for j in range(phrase_len):
                        detected_indices.add(i + j)
                    context_start = max(0, i - 3)
                    context_end = min(len(words), i + phrase_len + 3)
                    context = " ".join(w["word"] for w in words[context_start:context_end])
                    detections.append({
                        "word": " ".join(words[i + j]["word"] for j in range(phrase_len)),
                        "start": words[i]["start"],
                        "end": words[i + phrase_len - 1]["end"],
                        "detection_source": "transcript_lexical",
                        "start_frame": words[i].get("start_frame"),
                        "end_frame": words[i + phrase_len - 1].get("end_frame"),
                        "source_start_frame": words[i].get("source_start_frame"),
                        "source_end_frame": words[i + phrase_len - 1].get("source_end_frame"),
                        "context": context,
                        "selected": True,
                        "_first_index": i,
                        "_last_index": i + phrase_len - 1,
                    })

        detections.sort(key=lambda d: d["start"])
        logger.info(
            "detect_filler_words: found %d fillers in %d words: %s",
            len(detections),
            len(words),
            [(d["word"], round(float(d["start"]), 2)) for d in detections[:20]],
        )
        detected_word_indices = set(detected_indices)

        def _find_neighbor_word(start_index: int, step: int) -> dict | None:
            probe = start_index
            while 0 <= probe < len(words):
                if probe not in detected_word_indices:
                    return words[probe]
                probe += step
            return None

        for detection in detections:
            first_index = int(detection.pop("_first_index", 0))
            last_index = int(detection.pop("_last_index", first_index))

            prev_word = _find_neighbor_word(first_index - 1, -1)
            next_word = _find_neighbor_word(last_index + 1, 1)

            if prev_word is not None:
                detection["prev_word"] = prev_word.get("word")
                if prev_word.get("end") is not None:
                    detection["prev_word_end"] = prev_word.get("end")
                if prev_word.get("end_frame") is not None:
                    detection["prev_word_end_frame"] = prev_word.get("end_frame")
                if prev_word.get("source_end_frame") is not None:
                    detection["prev_word_source_end_frame"] = prev_word.get("source_end_frame")

            if next_word is not None:
                detection["next_word"] = next_word.get("word")
                if next_word.get("start") is not None:
                    detection["next_word_start"] = next_word.get("start")
                if next_word.get("start_frame") is not None:
                    detection["next_word_start_frame"] = next_word.get("start_frame")
                if next_word.get("source_start_frame") is not None:
                    detection["next_word_source_start_frame"] = next_word.get("source_start_frame")

        return detections

    def detect_hesitation_audio_candidates(
        self,
        audio_path: str,
        transcript: dict,
        filler_words: list[str] | None = None,
        min_duration_ms: int = 80,
        max_duration_ms: int = 420,
        min_gap_ms: int = 45,
        max_gap_ms: int = 950,
        sample_rate: int = 16000,
        hop_ms: int = 10,
    ) -> list[dict]:
        """
        Recover likely filler hesitations that the transcript missed entirely.

        This looks for short voiced speech islands inside transcript gaps and
        returns them as confident `um` / `uh` markers. It is intentionally
        precision-biased: candidates must live in real word gaps, avoid
        repetition/stutter contexts, and match a tight low-complexity voiced
        hesitation profile.
        """
        try:
            import librosa
        except Exception:
            return []

        allowed_families = self._allowed_hesitation_families(filler_words)
        if not allowed_families:
            return []
        debug_enabled = bool(getattr(settings, "debug_cut_planning", False))

        transcript_words = [
            word for word in (transcript.get("words", []) or [])
            if isinstance(word.get("word"), str) and not word["word"].startswith("[")
        ]
        if not transcript_words:
            return []

        sorted_words = sorted(
            (
                {
                    "word": str(word.get("word", "")).strip(),
                    "start": float(word.get("start", 0.0)),
                    "end": float(word.get("end", word.get("start", 0.0))),
                }
                for word in transcript_words
                if word.get("start") is not None and word.get("end") is not None
            ),
            key=lambda word: (word["start"], word["end"]),
        )
        if not sorted_words:
            return []

        try:
            y, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
        except Exception as e:
            logger.warning("Audio hesitation recovery skipped for %s: %s", audio_path, e)
            return []

        if y.size == 0:
            return []

        hop_length, frame_length, n_fft = _stft_window_params(sr, hop_ms)
        S = np.abs(
            librosa.stft(
                y,
                n_fft=n_fft,
                hop_length=hop_length,
                win_length=frame_length,
                center=True,
            )
        )
        if S.size == 0:
            return []

        rms = librosa.feature.rms(S=S, frame_length=n_fft)[0]
        if rms.size == 0:
            return []

        flatness = librosa.feature.spectral_flatness(S=np.maximum(S, 1e-10))[0]
        centroid = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
        rolloff = librosa.feature.spectral_rolloff(S=S, sr=sr)[0]
        zcr = librosa.feature.zero_crossing_rate(
            y,
            frame_length=frame_length,
            hop_length=hop_length,
            center=True,
        )[0]

        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        power = S ** 2
        total_power = np.mean(power, axis=0) + 1e-9
        speech_band_mask = (freqs >= 120.0) & (freqs <= 4500.0)
        high_band_mask = (freqs >= 3200.0) & (freqs <= min(float(sr) / 2.0, 9000.0))
        speech_band_power = np.mean(power[speech_band_mask], axis=0) if np.any(speech_band_mask) else total_power
        high_band_power = np.mean(power[high_band_mask], axis=0) if np.any(high_band_mask) else total_power
        speech_band_ratio = speech_band_power / total_power
        high_band_ratio = high_band_power / total_power
        times = librosa.frames_to_time(
            np.arange(S.shape[1]),
            sr=sr,
            hop_length=hop_length,
            n_fft=n_fft,
        )

        def _robust_norm(values: np.ndarray) -> np.ndarray:
            low = float(np.percentile(values, 10))
            high = float(np.percentile(values, 90))
            if high - low < 1e-9:
                return np.zeros_like(values, dtype=np.float32)
            return np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)

        speech_score = (
            0.32 * _robust_norm(rms)
            + 0.20 * _robust_norm(speech_band_ratio)
            + 0.12 * _robust_norm(high_band_ratio)
            + 0.14 * (1.0 - _robust_norm(flatness))
            + 0.12 * (1.0 - _robust_norm(zcr))
            + 0.10 * (1.0 - _robust_norm(centroid))
        )
        smooth_frames = max(3, int(round(0.05 / max(hop_ms / 1000.0, 1e-3))))
        kernel = np.ones(smooth_frames, dtype=np.float32) / float(smooth_frames)
        speech_score = np.convolve(speech_score, kernel, mode="same")

        dbfs = 20.0 * np.log10(np.maximum(rms, 1e-6))
        global_speech_threshold = max(
            float(np.percentile(speech_score, 50)),
            float(np.percentile(speech_score, 58)) * 0.72,
        )
        global_db_floor = max(-60.0, float(np.percentile(dbfs, 22)) - 4.0)
        duration_seconds = len(y) / sr
        min_gap_seconds = min_gap_ms / 1000.0
        max_gap_seconds = max_gap_ms / 1000.0
        min_duration_seconds = min_duration_ms / 1000.0
        max_duration_seconds = max_duration_ms / 1000.0
        hop_seconds = hop_ms / 1000.0

        def _extract_intervals(mask: np.ndarray) -> list[tuple[float, float]]:
            intervals: list[tuple[float, float]] = []
            run_start = None
            for idx, flag in enumerate(mask):
                if flag and run_start is None:
                    run_start = idx
                elif not flag and run_start is not None:
                    end_idx = max(run_start, idx - 1)
                    start_time = float(times[run_start])
                    end_time = float(times[end_idx] + hop_seconds)
                    intervals.append((start_time, end_time))
                    run_start = None
            if run_start is not None:
                intervals.append((float(times[run_start]), float(times[-1] + hop_seconds)))
            return intervals

        gap_windows: list[dict[str, Any]] = []
        first_word = sorted_words[0]
        if first_word["start"] >= min_gap_seconds:
            lead_end = min(first_word["start"], max_gap_seconds)
            gap_windows.append({
                "start": 0.0,
                "end": lead_end,
                "context": f"before '{first_word['word']}'",
                "next_word": first_word,
            })

        for prev_word, next_word in zip(sorted_words, sorted_words[1:]):
            gap_start = max(0.0, float(prev_word["end"]))
            gap_end = min(duration_seconds, float(next_word["start"]))
            gap_duration = gap_end - gap_start
            if gap_duration < min_gap_seconds or gap_duration > max_gap_seconds:
                continue
            gap_windows.append({
                "start": gap_start,
                "end": gap_end,
                "context": f"between '{prev_word['word']}' and '{next_word['word']}'",
                "prev_word": prev_word,
                "next_word": next_word,
            })

        last_word = sorted_words[-1]
        tail_gap = duration_seconds - float(last_word["end"])
        if tail_gap >= min_gap_seconds:
            tail_start = max(float(last_word["end"]), duration_seconds - max_gap_seconds)
            gap_windows.append({
                "start": tail_start,
                "end": duration_seconds,
                "context": f"after '{last_word['word']}'",
                "prev_word": last_word,
            })

        if not gap_windows:
            return []

        detections: list[dict] = []
        for gap in gap_windows:
            gap_start = float(gap["start"])
            gap_end = float(gap["end"])
            context = str(gap["context"])
            prev_word = gap.get("prev_word")
            next_word = gap.get("next_word")
            gap_debug_rejections: list[str] = []

            def _record_gap_rejection(reason: str) -> None:
                if debug_enabled and reason not in gap_debug_rejections and len(gap_debug_rejections) < 8:
                    gap_debug_rejections.append(reason)

            if self._is_repetition_or_stutter_context(prev_word, next_word):
                continue

            gap_duration = gap_end - gap_start
            if gap_duration < min_gap_seconds or gap_duration > max_gap_seconds:
                continue

            context_start = max(0.0, gap_start - hop_seconds)
            context_end = min(duration_seconds, gap_end + hop_seconds)
            idx = np.where((times >= context_start) & (times <= context_end))[0]
            if idx.size == 0:
                continue

            local_speech_threshold = max(
                global_speech_threshold * 0.88,
                float(np.percentile(speech_score[idx], 65)) * 0.68,
            )
            local_db_floor = max(
                global_db_floor,
                float(np.percentile(dbfs[idx], 60)) - 12.0,
            )
            gap_mask = (times >= gap_start) & (times <= gap_end)
            candidate_masks = [
                (
                    "strict",
                    gap_mask
                    & (speech_score >= local_speech_threshold)
                    & (dbfs >= local_db_floor)
                    & (flatness <= 0.42)
                    & (zcr <= 0.19)
                    & (centroid <= 2050.0)
                    & (rolloff <= 3400.0)
                    & (high_band_ratio <= 0.22)
                ),
            ]
            if not any(np.any(mask) for _, mask in candidate_masks):
                _record_gap_rejection("no_candidate_mask")

            best_detection = None
            best_score = float("-inf")
            for mask_name, candidate_mask in candidate_masks:
                if not np.any(candidate_mask):
                    continue
                for cand_start, cand_end in _extract_intervals(candidate_mask):
                    start = max(gap_start, cand_start)
                    end = min(gap_end, cand_end)
                    duration = end - start
                    whole_gap_duration_seconds = max(min_duration_seconds, 0.16)
                    if duration < whole_gap_duration_seconds or duration > max_duration_seconds:
                        _record_gap_rejection(f"duration:{duration:.3f}")
                        continue

                    local_interval_idx = np.where((times >= start) & (times <= end))[0]
                    if local_interval_idx.size == 0:
                        _record_gap_rejection("empty_interval")
                        continue

                    avg_centroid = float(np.mean(centroid[local_interval_idx]))
                    avg_rolloff = float(np.mean(rolloff[local_interval_idx]))
                    avg_flatness = float(np.mean(flatness[local_interval_idx]))
                    avg_zcr = float(np.mean(zcr[local_interval_idx]))
                    avg_high_band = float(np.mean(high_band_ratio[local_interval_idx]))
                    candidate_db = float(np.mean(dbfs[local_interval_idx]))
                    whole_gap_hesitation = (
                        gap_duration >= 0.24
                        and gap_duration <= 0.34
                        and duration >= gap_duration * 0.68
                        and avg_centroid >= 850.0
                        and avg_centroid <= 1600.0
                        and avg_rolloff <= 2800.0
                        and avg_flatness <= 0.10
                        and avg_zcr >= 0.050
                        and avg_zcr <= 0.14
                        and avg_high_band <= 0.08
                    )
                    if duration >= gap_duration * 0.72 and not whole_gap_hesitation:
                        _record_gap_rejection("too_much_gap")
                        continue

                    margin_window = min(0.05, max(0.025, duration * 0.8))
                    left_idx = np.where(
                        (times >= max(gap_start, start - margin_window))
                        & (times <= start - 0.005)
                    )[0]
                    right_idx = np.where(
                        (times >= end + 0.005)
                        & (times <= min(gap_end, end + margin_window))
                    )[0]
                    lead_margin = max(0.0, start - gap_start)
                    trail_margin = max(0.0, gap_end - end)
                    edge_slack = min(0.14, max(0.05, gap_duration * 0.22))
                    if lead_margin <= edge_slack and trail_margin > edge_slack:
                        edge_mode = "left"
                    elif trail_margin <= edge_slack and lead_margin > edge_slack:
                        edge_mode = "right"
                    else:
                        edge_mode = "center"

                    require_left_quiet = edge_mode in {"center", "right"}
                    require_right_quiet = edge_mode in {"center", "left"}
                    if require_left_quiet and left_idx.size < 2 and not whole_gap_hesitation:
                        _record_gap_rejection(f"left_margin:{left_idx.size}")
                        continue
                    if require_right_quiet and right_idx.size < 2 and not whole_gap_hesitation:
                        _record_gap_rejection(f"right_margin:{right_idx.size}")
                        continue

                    left_quiet = True
                    right_quiet = True
                    if left_idx.size >= 2:
                        left_speech = float(np.mean(speech_score[left_idx]))
                        left_db = float(np.mean(dbfs[left_idx]))
                        left_quiet = (
                            (
                                left_speech <= local_speech_threshold * 0.84
                                and left_db <= local_db_floor + 6.0
                            )
                            or (
                                left_db <= candidate_db - 9.0
                                and left_speech <= local_speech_threshold * 1.02
                            )
                        )
                    if right_idx.size >= 2:
                        right_speech = float(np.mean(speech_score[right_idx]))
                        right_db = float(np.mean(dbfs[right_idx]))
                        right_quiet = (
                            (
                                right_speech <= local_speech_threshold * 0.84
                                and right_db <= local_db_floor + 6.0
                            )
                            or (
                                right_db <= candidate_db - 9.0
                                and right_speech <= local_speech_threshold * 1.02
                            )
                        )

                    if require_left_quiet and not left_quiet and not whole_gap_hesitation:
                        _record_gap_rejection("left_margin_speech")
                        continue
                    if require_right_quiet and not right_quiet and not whole_gap_hesitation:
                        _record_gap_rejection("right_margin_speech")
                        continue

                    # Prefer simple voiced hesitations instead of sibilants, clicks, or
                    # clipped consonant tails. These thresholds are intentionally biased
                    # toward "um/uh/mhm" style sounds.
                    if avg_centroid > 2100.0 or avg_rolloff > 3600.0:
                        _record_gap_rejection("spectral_bright")
                        continue
                    if avg_flatness > 0.42 or avg_zcr > 0.20 or avg_high_band > 0.24:
                        _record_gap_rejection("spectral_noisy")
                        continue
                    if not whole_gap_hesitation and (avg_centroid < 800.0 or avg_zcr < 0.045):
                        _record_gap_rejection("too_monotone")
                        continue
                    weak_context_tokens = {"in", "is", "of", "on", "or", "to"}
                    prev_token = self._clean_filler_token(prev_word.get("word", "")) if isinstance(prev_word, dict) else ""
                    next_token = self._clean_filler_token(next_word.get("word", "")) if isinstance(next_word, dict) else ""
                    prev_text = str(prev_word.get("word", "")).rstrip() if isinstance(prev_word, dict) else ""
                    if (
                        not whole_gap_hesitation
                        and not prev_text.endswith((".", "?", "!", ",", ";", ":"))
                        and (prev_token in weak_context_tokens or next_token in weak_context_tokens)
                        and (duration < 0.20 or avg_centroid < 850.0 or avg_zcr < 0.060)
                    ):
                        _record_gap_rejection("weak_common_word_context")
                        continue

                    overlaps_existing = any(
                        max(start, word["start"]) < min(end, word["end"]) for word in sorted_words
                    )
                    if overlaps_existing:
                        _record_gap_rejection("overlaps_word")
                        continue

                    estimated_label = self._estimate_audio_hesitation_label(
                        allowed_families,
                        duration,
                        avg_centroid,
                        avg_rolloff,
                        avg_zcr,
                        avg_high_band,
                    )
                    if estimated_label not in {"um", "uh"}:
                        _record_gap_rejection(f"label:{estimated_label}")
                        continue
                    family_match = self._matches_confident_hesitation_family(
                        estimated_label,
                        duration,
                        avg_centroid,
                        avg_rolloff,
                        avg_zcr,
                        avg_high_band,
                        avg_flatness,
                    )
                    if (
                        not family_match
                        and estimated_label == "um"
                        and 0.08 <= duration <= max_duration_seconds
                        and avg_centroid <= 1600.0
                        and avg_rolloff <= 2800.0
                        and avg_zcr <= 0.12
                        and avg_high_band <= 0.10
                        and avg_flatness <= 0.20
                    ):
                        family_match = True
                    if not family_match:
                        _record_gap_rejection("family_mismatch")
                        continue

                    score = (
                        duration * 2.0
                        + (0.18 if edge_mode != "center" else 0.0)
                        + (0.10 if whole_gap_hesitation else 0.0)
                        + max(0.0, 1800.0 - avg_centroid) / 1800.0
                        + max(0.0, 3200.0 - avg_rolloff) / 3200.0
                        + max(0.0, 0.22 - avg_high_band) * 3.0
                        + max(0.0, 0.20 - avg_flatness) * 2.0
                    )
                    detection = {
                        "word": estimated_label,
                        "start": round(start, 4),
                        "end": round(end, 4),
                        "duration": round(duration, 4),
                        "context": context,
                        "detection_source": "audio_gap",
                        "gap_edge_mode": edge_mode,
                        "gap_mask_source": mask_name,
                        "prev_word": prev_word.get("word") if isinstance(prev_word, dict) else None,
                        "prev_word_end": prev_word.get("end") if isinstance(prev_word, dict) else None,
                        "prev_word_end_frame": prev_word.get("end_frame") if isinstance(prev_word, dict) else None,
                        "prev_word_source_end_frame": prev_word.get("source_end_frame") if isinstance(prev_word, dict) else None,
                        "next_word": next_word.get("word") if isinstance(next_word, dict) else None,
                        "next_word_start": next_word.get("start") if isinstance(next_word, dict) else None,
                        "next_word_start_frame": next_word.get("start_frame") if isinstance(next_word, dict) else None,
                        "next_word_source_start_frame": next_word.get("source_start_frame") if isinstance(next_word, dict) else None,
                        "selected": True,
                        "kind": "audio_hesitation",
                    }
                    if debug_enabled and gap_debug_rejections:
                        detection["debug_nearby_rejections"] = list(gap_debug_rejections)
                    if score > best_score:
                        best_detection = detection
                        best_score = score

            prev_text = str(prev_word.get("word", "")).rstrip() if isinstance(prev_word, dict) else ""
            rescue_allowed = prev_text.endswith((".", "?", "!", ","))
            if best_detection is None and rescue_allowed and gap_duration >= max(0.30, min_gap_seconds):
                rescue_speech_threshold = max(
                    global_speech_threshold * 0.62,
                    float(np.percentile(speech_score[idx], 45)) * 0.54,
                )
                rescue_db_floor = max(
                    global_db_floor - 2.0,
                    local_db_floor - 12.0,
                )
                rescue_mask = (
                    gap_mask
                    & (speech_score >= rescue_speech_threshold)
                    & (dbfs >= rescue_db_floor)
                    & (flatness <= 0.12)
                    & (zcr <= 0.20)
                    & (centroid <= 2300.0)
                    & (rolloff <= 4700.0)
                    & (high_band_ratio <= 0.10)
                )
                rescue_intervals = self._merge_nearby_intervals(
                    self._extract_intervals_from_mask(
                        rescue_mask,
                        times,
                        hop_seconds,
                        min_frames=max(2, int(round(0.02 / max(hop_seconds, 1e-6)))),
                    ),
                    max_gap_seconds=0.03,
                )
                rescue_threshold = gap_start + gap_duration * 0.65
                rescue_intervals = [
                    (float(interval_start), float(interval_end))
                    for interval_start, interval_end in rescue_intervals
                    if float(interval_end) >= rescue_threshold
                ]
                for cand_start, cand_end in sorted(rescue_intervals, key=lambda interval: (float(interval[1]), float(interval[0])), reverse=True):
                    start = max(gap_start, float(cand_start))
                    end = min(gap_end, float(cand_end))
                    duration = end - start
                    if duration < 0.02 or duration > 0.16:
                        continue

                    local_interval_idx = np.where((times >= start) & (times <= end))[0]
                    if local_interval_idx.size == 0:
                        continue

                    avg_centroid = float(np.mean(centroid[local_interval_idx]))
                    avg_rolloff = float(np.mean(rolloff[local_interval_idx]))
                    avg_flatness = float(np.mean(flatness[local_interval_idx]))
                    avg_zcr = float(np.mean(zcr[local_interval_idx]))
                    avg_high_band = float(np.mean(high_band_ratio[local_interval_idx]))
                    if (
                        avg_centroid < 850.0
                        or avg_centroid > 2300.0
                        or avg_rolloff > 4700.0
                        or avg_flatness > 0.14
                        or avg_zcr < 0.045
                        or avg_zcr > 0.20
                        or avg_high_band > 0.10
                    ):
                        continue

                    estimated_label = self._estimate_audio_hesitation_label(
                        allowed_families,
                        max(duration, 0.08),
                        avg_centroid,
                        avg_rolloff,
                        avg_zcr,
                        avg_high_band,
                    )
                    if estimated_label not in {"um", "uh"}:
                        continue

                    best_detection = {
                        "word": estimated_label,
                        "start": round(start, 4),
                        "end": round(end, 4),
                        "duration": round(duration, 4),
                        "context": context,
                        "detection_source": "audio_gap",
                        "gap_edge_mode": "right",
                        "gap_mask_source": "rescue",
                        "prev_word": prev_word.get("word") if isinstance(prev_word, dict) else None,
                        "prev_word_end": prev_word.get("end") if isinstance(prev_word, dict) else None,
                        "prev_word_end_frame": prev_word.get("end_frame") if isinstance(prev_word, dict) else None,
                        "prev_word_source_end_frame": prev_word.get("source_end_frame") if isinstance(prev_word, dict) else None,
                        "next_word": next_word.get("word") if isinstance(next_word, dict) else None,
                        "next_word_start": next_word.get("start") if isinstance(next_word, dict) else None,
                        "next_word_start_frame": next_word.get("start_frame") if isinstance(next_word, dict) else None,
                        "next_word_source_start_frame": next_word.get("source_start_frame") if isinstance(next_word, dict) else None,
                        "selected": True,
                        "kind": "audio_hesitation",
                    }
                    break

            if best_detection is not None:
                detections.append(best_detection)
            elif debug_enabled and gap_debug_rejections:
                logger.debug("gap hesitation miss %s: %s", context, ", ".join(gap_debug_rejections))

        detections.sort(key=lambda detection: (detection["start"], detection["end"]))

        merged: list[dict] = []
        for detection in detections:
            if not merged:
                merged.append(detection)
                continue
            previous = merged[-1]
            if detection["start"] <= previous["end"] + 0.02:
                previous["end"] = round(max(previous["end"], detection["end"]), 4)
                previous["duration"] = round(previous["end"] - previous["start"], 4)
            else:
                merged.append(detection)

        return merged

    def detect_attached_hesitation_onsets(
        self,
        transcript: dict,
        filler_words: list[str] | None = None,
        profile: dict[str, Any] | None = None,
        min_duration_ms: int = 70,
        max_duration_ms: int = 260,
        max_prefix_ms: int = 280,
        max_backtrack_ms: int = 80,
        min_remaining_word_ms: int = 90,
    ) -> list[dict]:
        """
        Recover likely attached `uh`/`um` hesitations that were blended into
        the start of the following spoken word instead of becoming their own
        transcript token.

        This pass is intentionally narrow. It only targets `uh`/`um` families,
        only inspects the onset of existing spoken words, and only accepts a
        detection when the onset looks like a low-complexity hesitation that
        then shifts into clearer continuation speech.
        """
        allowed_families = self._allowed_hesitation_families(filler_words) & {"um", "uh"}
        if not allowed_families or profile is None:
            return []

        times = profile.get("times")
        speech_score = profile.get("speech_score")
        dbfs = profile.get("dbfs")
        flatness = profile.get("flatness")
        centroid = profile.get("centroid")
        rolloff = profile.get("rolloff")
        zcr = profile.get("zcr")
        high_band_ratio = profile.get("high_band_ratio")
        duration = float(profile.get("duration") or 0.0)
        hop_seconds = float(profile.get("hop_seconds") or 0.0)
        if (
            times is None or speech_score is None or dbfs is None or flatness is None
            or centroid is None or rolloff is None or zcr is None or high_band_ratio is None
            or hop_seconds <= 0.0 or duration <= 0.0
        ):
            return []

        transcript_words = []
        for raw_word in (transcript.get("words", []) or []):
            text = str(raw_word.get("word", "")).strip()
            if not text or text.startswith("["):
                continue
            try:
                start = max(0.0, min(duration, float(raw_word.get("start", 0.0))))
                end = max(start, min(duration, float(raw_word.get("end", start))))
            except Exception:
                continue
            transcript_words.append({
                **raw_word,
                "word": text,
                "start": start,
                "end": end,
            })

        if not transcript_words:
            return []

        transcript_words.sort(key=lambda word: (float(word["start"]), float(word["end"])))

        global_speech_threshold = max(
            float(np.percentile(speech_score, 48)),
            float(np.percentile(speech_score, 60)) * 0.74,
        )
        global_db_floor = max(-62.0, float(np.percentile(dbfs, 24)) - 5.0)
        min_duration_seconds = min_duration_ms / 1000.0
        max_duration_seconds = max_duration_ms / 1000.0
        max_prefix_seconds = max_prefix_ms / 1000.0
        max_backtrack_seconds = max_backtrack_ms / 1000.0
        min_remaining_word_seconds = min_remaining_word_ms / 1000.0
        min_frames = max(2, int(round(min_duration_seconds / max(hop_seconds, 1e-6))))

        detections: list[dict] = []
        for index, word in enumerate(transcript_words):
            cleaned = self._clean_filler_token(word["word"])
            if not cleaned:
                continue
            if self._hesitation_family(cleaned) in allowed_families:
                # Already a standalone hesitation token.
                continue

            word_start = float(word["start"])
            word_end = float(word["end"])
            word_duration = word_end - word_start
            if word_duration < (min_duration_seconds + min_remaining_word_seconds):
                continue

            prev_word = transcript_words[index - 1] if index > 0 else None
            next_word = transcript_words[index + 1] if index < len(transcript_words) - 1 else None
            prev_word_end = float(prev_word["end"]) if prev_word is not None else 0.0
            prev_cleaned = self._clean_filler_token(prev_word["word"]) if isinstance(prev_word, dict) else ""
            next_cleaned = self._clean_filler_token(next_word["word"]) if isinstance(next_word, dict) else ""
            if self._is_repetition_or_stutter_context(prev_word, word) or self._is_repetition_or_stutter_context(word, next_word):
                continue
            if (prev_cleaned and prev_cleaned == cleaned) or (next_cleaned and next_cleaned == cleaned):
                # Immediate repetitions are handled by repetition detection; do not
                # reinterpret them as onset fillers.
                continue

            search_start = max(prev_word_end, word_start - max_backtrack_seconds)
            search_end = min(
                duration,
                word_start + min(max_prefix_seconds, max(word_duration * 0.55, min_duration_seconds + 0.03)),
            )
            if search_end - search_start < min_duration_seconds:
                continue

            window_idx = np.where((times >= search_start) & (times <= search_end))[0]
            if window_idx.size < min_frames:
                continue

            local_speech = speech_score[window_idx]
            local_dbfs = dbfs[window_idx]
            speech_threshold = max(
                global_speech_threshold * 0.92,
                float(np.percentile(local_speech, 55)) * 0.78,
            )
            db_floor = max(
                global_db_floor,
                float(np.percentile(local_dbfs, 35)) - 8.0,
            )

            base_mask = (times >= search_start) & (times <= search_end)
            onset_mask = (
                base_mask
                & (speech_score >= speech_threshold)
                & (dbfs >= db_floor)
                & (flatness <= 0.34)
                & (zcr <= 0.12)
                & (centroid <= 1550.0)
                & (rolloff <= 2500.0)
                & (high_band_ratio <= 0.12)
            )
            candidate_intervals = self._merge_nearby_intervals(
                self._extract_intervals_from_mask(
                    onset_mask,
                    times,
                    hop_seconds,
                    min_frames=min_frames,
                ),
                max_gap_seconds=0.02,
            )
            if not candidate_intervals:
                continue

            best_detection = None
            best_score = float("-inf")

            for cand_start, cand_end in candidate_intervals:
                cand_start = max(search_start, float(cand_start))
                cand_end = min(search_end, float(cand_end))
                candidate_duration = cand_end - cand_start
                if candidate_duration < min_duration_seconds or candidate_duration > max_duration_seconds:
                    continue
                start_slop_seconds = max(0.02, hop_seconds * 2.0)
                if cand_start > word_start + start_slop_seconds:
                    continue
                if cand_end <= word_start + 0.035:
                    continue
                max_prefix_end = word_start + min(0.16, max(0.09, word_duration * 0.28))
                if cand_end > max_prefix_end:
                    continue
                if word_end - cand_end < min_remaining_word_seconds:
                    continue

                candidate_idx = np.where((times >= cand_start) & (times <= cand_end))[0]
                if candidate_idx.size < min_frames:
                    continue

                continuation_start = min(word_end, cand_end + 0.01)
                continuation_end = min(word_end, cand_end + 0.14)
                continuation_idx = np.where((times >= continuation_start) & (times <= continuation_end))[0]
                if continuation_idx.size < 2:
                    continue

                lead_window = min(0.06, max(0.02, candidate_duration * 0.9))
                lead_start = max(
                    0.0,
                    max(search_start, cand_start - lead_window) - (hop_seconds * 0.55),
                )
                lead_end = cand_start - (hop_seconds * 0.15)
                pre_idx = np.where(
                    (times >= lead_start)
                    & (times < lead_end)
                )[0]
                if pre_idx.size >= 1:
                    pre_speech = float(np.mean(speech_score[pre_idx]))
                    pre_db = float(np.mean(dbfs[pre_idx]))
                    if pre_speech > speech_threshold * 0.70 or pre_db > db_floor + 4.5:
                        continue

                candidate_speech = float(np.mean(speech_score[candidate_idx]))
                continuation_speech = float(np.mean(speech_score[continuation_idx]))
                if continuation_speech < speech_threshold * 0.72:
                    continue
                if continuation_speech < candidate_speech + 0.05:
                    continue

                avg_centroid = float(np.mean(centroid[candidate_idx]))
                avg_rolloff = float(np.mean(rolloff[candidate_idx]))
                avg_flatness = float(np.mean(flatness[candidate_idx]))
                avg_zcr = float(np.mean(zcr[candidate_idx]))
                avg_high_band = float(np.mean(high_band_ratio[candidate_idx]))
                if avg_flatness > 0.38 or avg_zcr > 0.16 or avg_high_band > 0.18:
                    continue

                continuation_centroid = float(np.mean(centroid[continuation_idx]))
                continuation_rolloff = float(np.mean(rolloff[continuation_idx]))
                continuation_zcr = float(np.mean(zcr[continuation_idx]))
                continuation_high_band = float(np.mean(high_band_ratio[continuation_idx]))
                has_spectral_shift = (
                    continuation_centroid >= avg_centroid + 120.0
                    or continuation_rolloff >= avg_rolloff + 180.0
                    or continuation_zcr >= avg_zcr + 0.015
                    or continuation_high_band >= avg_high_band + 0.015
                )
                if not has_spectral_shift:
                    continue

                estimated_label = self._estimate_audio_hesitation_label(
                    allowed_families,
                    candidate_duration,
                    avg_centroid,
                    avg_rolloff,
                    avg_zcr,
                    avg_high_band,
                )
                if (
                    estimated_label == "um"
                    and candidate_duration <= 0.16
                    and not (
                        avg_centroid <= 1250.0
                        and avg_rolloff <= 2000.0
                        and avg_high_band <= 0.10
                    )
                ):
                    estimated_label = "uh"
                if estimated_label not in {"um", "uh"}:
                    continue
                if not self._matches_confident_hesitation_family(
                    estimated_label,
                    candidate_duration,
                    avg_centroid,
                    avg_rolloff,
                    avg_zcr,
                    avg_high_band,
                    avg_flatness,
                ):
                    continue

                score = (
                    candidate_duration * 2.0
                    + max(0.0, continuation_centroid - avg_centroid) / 1000.0
                    + max(0.0, continuation_rolloff - avg_rolloff) / 1500.0
                    + max(0.0, continuation_high_band - avg_high_band) * 4.0
                )

                detection = {
                    "word": estimated_label,
                    "start": round(cand_start, 4),
                    "end": round(cand_end, 4),
                    "duration": round(candidate_duration, 4),
                    "context": f"attached to '{word['word']}'",
                    "detection_source": "audio_attached_onset",
                    "kind": "audio_hesitation",
                    "attachment": "word_onset",
                    "attached_word": word.get("word"),
                    "attached_word_start": word_start,
                    "attached_word_end": word_end,
                    "attached_word_start_frame": word.get("start_frame"),
                    "attached_word_source_start_frame": word.get("source_start_frame"),
                    "selected": True,
                    "boundary_source": "waveform",
                    "prev_word": prev_word.get("word") if isinstance(prev_word, dict) else None,
                    "prev_word_end": prev_word.get("end") if isinstance(prev_word, dict) else None,
                    "prev_word_end_frame": prev_word.get("end_frame") if isinstance(prev_word, dict) else None,
                    "prev_word_source_end_frame": prev_word.get("source_end_frame") if isinstance(prev_word, dict) else None,
                }

                detection["start_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_start,
                    "start_frame",
                    "end_frame",
                )
                detection["end_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_end,
                    "start_frame",
                    "end_frame",
                )
                detection["source_start_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_start,
                    "source_start_frame",
                    "source_end_frame",
                )
                detection["source_end_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_end,
                    "source_start_frame",
                    "source_end_frame",
                )

                if score > best_score:
                    best_detection = detection
                    best_score = score

            if best_detection is not None:
                detections.append(best_detection)

        detections.sort(key=lambda detection: (float(detection["start"]), float(detection["end"])))
        return detections

    def detect_attached_hesitation_offsets(
        self,
        transcript: dict,
        filler_words: list[str] | None = None,
        profile: dict[str, Any] | None = None,
        min_duration_ms: int = 70,
        max_duration_ms: int = 260,
        max_suffix_ms: int = 280,
        max_forward_ms: int = 80,
        min_leading_word_ms: int = 90,
    ) -> list[dict]:
        """
        Recover likely attached `uh`/`um` hesitations that were blended into
        the tail of the previous spoken word instead of becoming their own
        transcript token.

        This complements `detect_attached_hesitation_onsets` and is likewise
        precision-biased: it only accepts low-complexity hesitation-like tails
        near a word boundary, with clearer non-hesitation speech before them.
        """
        allowed_families = self._allowed_hesitation_families(filler_words) & {"um", "uh"}
        if not allowed_families or profile is None:
            return []
        debug_enabled = bool(getattr(settings, "debug_cut_planning", False))

        times = profile.get("times")
        speech_score = profile.get("speech_score")
        dbfs = profile.get("dbfs")
        flatness = profile.get("flatness")
        centroid = profile.get("centroid")
        rolloff = profile.get("rolloff")
        zcr = profile.get("zcr")
        high_band_ratio = profile.get("high_band_ratio")
        duration = float(profile.get("duration") or 0.0)
        hop_seconds = float(profile.get("hop_seconds") or 0.0)
        if (
            times is None or speech_score is None or dbfs is None or flatness is None
            or centroid is None or rolloff is None or zcr is None or high_band_ratio is None
            or hop_seconds <= 0.0 or duration <= 0.0
        ):
            return []

        transcript_words = []
        for raw_word in (transcript.get("words", []) or []):
            text = str(raw_word.get("word", "")).strip()
            if not text or text.startswith("["):
                continue
            try:
                start = max(0.0, min(duration, float(raw_word.get("start", 0.0))))
                end = max(start, min(duration, float(raw_word.get("end", start))))
            except Exception:
                continue
            transcript_words.append({
                **raw_word,
                "word": text,
                "start": start,
                "end": end,
            })

        if not transcript_words:
            return []

        transcript_words.sort(key=lambda word: (float(word["start"]), float(word["end"])))

        global_speech_threshold = max(
            float(np.percentile(speech_score, 48)),
            float(np.percentile(speech_score, 60)) * 0.74,
        )
        global_db_floor = max(-62.0, float(np.percentile(dbfs, 24)) - 5.0)
        min_duration_seconds = min_duration_ms / 1000.0
        max_duration_seconds = max_duration_ms / 1000.0
        max_suffix_seconds = max_suffix_ms / 1000.0
        max_forward_seconds = max_forward_ms / 1000.0
        min_leading_word_seconds = min_leading_word_ms / 1000.0
        min_frames = max(2, int(round(min_duration_seconds / max(hop_seconds, 1e-6))))
        near_zero_gap_seconds = max(0.02, hop_seconds * 3.0)

        detections: list[dict] = []
        for index, word in enumerate(transcript_words):
            cleaned = self._clean_filler_token(word["word"])
            if not cleaned:
                continue
            if self._hesitation_family(cleaned) in allowed_families:
                continue

            word_start = float(word["start"])
            word_end = float(word["end"])
            word_duration = word_end - word_start
            if word_duration < (min_duration_seconds + min_leading_word_seconds):
                continue

            prev_word = transcript_words[index - 1] if index > 0 else None
            next_word = transcript_words[index + 1] if index < len(transcript_words) - 1 else None
            next_word_start = float(next_word["start"]) if next_word is not None else duration
            next_word_gap = max(0.0, next_word_start - word_end) if next_word is not None else None
            word_debug_rejections: list[str] = []

            def _record_tail_rejection(reason: str) -> None:
                if debug_enabled and reason not in word_debug_rejections and len(word_debug_rejections) < 8:
                    word_debug_rejections.append(reason)

            prev_cleaned = self._clean_filler_token(prev_word["word"]) if isinstance(prev_word, dict) else ""
            next_cleaned = self._clean_filler_token(next_word["word"]) if isinstance(next_word, dict) else ""
            if self._is_repetition_or_stutter_context(prev_word, word) or self._is_repetition_or_stutter_context(word, next_word):
                continue
            if (prev_cleaned and prev_cleaned == cleaned) or (next_cleaned and next_cleaned == cleaned):
                continue
            if next_word_gap is not None and next_word_gap <= near_zero_gap_seconds:
                _record_tail_rejection("near_zero_gap")
                continue

            search_start = max(
                word_start,
                word_end - min(max_suffix_seconds, max(word_duration * 0.55, min_duration_seconds + 0.03)),
            )
            search_end = min(duration, max(word_end, next_word_start) + max_forward_seconds)
            if next_word is not None:
                search_end = min(search_end, next_word_start)
            if search_end - search_start < min_duration_seconds:
                continue

            window_idx = np.where((times >= search_start) & (times <= search_end))[0]
            if window_idx.size < min_frames:
                continue

            local_speech = speech_score[window_idx]
            local_dbfs = dbfs[window_idx]
            speech_threshold = max(
                global_speech_threshold * 0.90,
                float(np.percentile(local_speech, 55)) * 0.76,
            )
            db_floor = max(
                global_db_floor,
                float(np.percentile(local_dbfs, 35)) - 8.0,
            )

            base_mask = (times >= search_start) & (times <= search_end)
            tail_mask = (
                base_mask
                & (speech_score >= speech_threshold)
                & (dbfs >= db_floor)
                & (flatness <= 0.36)
                & (zcr <= 0.14)
                & (centroid <= 1700.0)
                & (rolloff <= 2800.0)
                & (high_band_ratio <= 0.15)
            )
            candidate_intervals = self._merge_nearby_intervals(
                self._extract_intervals_from_mask(
                    tail_mask,
                    times,
                    hop_seconds,
                    min_frames=min_frames,
                ),
                max_gap_seconds=0.02,
            )
            if not candidate_intervals:
                continue

            best_detection = None
            best_score = float("-inf")

            for cand_start, cand_end in candidate_intervals:
                cand_start = max(search_start, float(cand_start))
                cand_end = min(search_end, float(cand_end))
                candidate_duration = cand_end - cand_start
                if candidate_duration < min_duration_seconds or candidate_duration > max_duration_seconds:
                    _record_tail_rejection(f"duration:{candidate_duration:.3f}")
                    continue

                end_slop_seconds = max(0.02, hop_seconds * 2.0)
                if cand_end < word_end - end_slop_seconds:
                    _record_tail_rejection("ends_too_early")
                    continue
                if cand_start >= word_end - 0.035:
                    _record_tail_rejection("starts_too_late")
                    continue
                tail_extension = cand_end - word_end
                if tail_extension < max(0.015, hop_seconds * 1.5):
                    _record_tail_rejection("no_post_word_extension")
                    continue
                if cand_start - word_start < min_leading_word_seconds:
                    _record_tail_rejection("not_enough_lead")
                    continue

                candidate_idx = np.where((times >= cand_start) & (times <= cand_end))[0]
                if candidate_idx.size < min_frames:
                    _record_tail_rejection("candidate_window_too_small")
                    continue

                lead_start = max(word_start, cand_start - 0.14)
                lead_end = max(lead_start, cand_start - 0.01)
                lead_idx = np.where((times >= lead_start) & (times <= lead_end))[0]
                if lead_idx.size < 2:
                    _record_tail_rejection("missing_lead_window")
                    continue

                trail_start = min(search_end, cand_end + 0.005)
                trail_end = min(search_end, cand_end + 0.06)
                trail_idx = np.where((times >= trail_start) & (times <= trail_end))[0]
                if trail_idx.size < 2:
                    _record_tail_rejection("missing_trailing_window")
                    continue

                lead_speech = float(np.mean(speech_score[lead_idx]))
                lead_db = float(np.mean(dbfs[lead_idx]))
                if lead_speech < speech_threshold * 0.78 or lead_db < db_floor + 2.0:
                    _record_tail_rejection("weak_lead")
                    continue

                candidate_speech = float(np.mean(speech_score[candidate_idx]))
                avg_centroid = float(np.mean(centroid[candidate_idx]))
                avg_rolloff = float(np.mean(rolloff[candidate_idx]))
                avg_flatness = float(np.mean(flatness[candidate_idx]))
                avg_zcr = float(np.mean(zcr[candidate_idx]))
                avg_high_band = float(np.mean(high_band_ratio[candidate_idx]))
                if avg_flatness > 0.40 or avg_zcr > 0.17 or avg_high_band > 0.19:
                    _record_tail_rejection("candidate_noisy")
                    continue

                lead_centroid = float(np.mean(centroid[lead_idx]))
                lead_rolloff = float(np.mean(rolloff[lead_idx]))
                lead_zcr = float(np.mean(zcr[lead_idx]))
                lead_high_band = float(np.mean(high_band_ratio[lead_idx]))
                has_spectral_shift = (
                    lead_centroid >= avg_centroid + 90.0
                    or lead_rolloff >= avg_rolloff + 150.0
                    or lead_zcr >= avg_zcr + 0.012
                    or lead_high_band >= avg_high_band + 0.012
                )
                if not has_spectral_shift:
                    _record_tail_rejection("missing_shift")
                    continue

                trail_speech = float(np.mean(speech_score[trail_idx]))
                trail_db = float(np.mean(dbfs[trail_idx]))
                strict_tail_gap_seconds = max(0.06, candidate_duration * 0.35)
                if next_word_gap is None or next_word_gap <= strict_tail_gap_seconds:
                    if trail_speech > speech_threshold * 0.78:
                        _record_tail_rejection("trail_speech_strict")
                        continue
                    if trail_db > db_floor + 3.5:
                        _record_tail_rejection("trail_db_strict")
                        continue
                else:
                    post_gap_end = min(search_end, next_word_start)
                    post_gap_idx = np.where(
                        (times >= max(cand_end + 0.005, trail_start))
                        & (times <= max(cand_end + 0.005, post_gap_end - 0.005))
                    )[0]
                    if post_gap_idx.size < 2:
                        _record_tail_rejection("missing_post_gap_window")
                        continue
                    valley_min_frames = max(2, int(round(0.02 / max(hop_seconds, 1e-6))))
                    quiet_mask = (
                        (times >= max(cand_end + 0.005, trail_start))
                        & (times <= post_gap_end)
                        & (speech_score <= speech_threshold * 0.76)
                        & (dbfs <= db_floor + 6.0)
                    )
                    quiet_intervals = self._extract_intervals_from_mask(
                        quiet_mask,
                        times,
                        hop_seconds,
                        min_frames=valley_min_frames,
                    )
                    if not quiet_intervals:
                        _record_tail_rejection("missing_post_gap_valley")
                        continue
                    quiet_start = min(float(interval[0]) for interval in quiet_intervals)
                    if quiet_start - cand_end > min(0.18, max(0.08, next_word_gap * 0.75)):
                        _record_tail_rejection("valley_too_late")
                        continue
                    if trail_speech > speech_threshold * 0.92 and trail_db > db_floor + 6.0:
                        _record_tail_rejection("immediate_trail_too_hot")
                        continue

                estimated_label = self._estimate_audio_hesitation_label(
                    allowed_families,
                    candidate_duration,
                    avg_centroid,
                    avg_rolloff,
                    avg_zcr,
                    avg_high_band,
                )
                if estimated_label not in {"um", "uh"}:
                    _record_tail_rejection(f"label:{estimated_label}")
                    continue
                if not self._matches_confident_hesitation_family(
                    estimated_label,
                    candidate_duration,
                    avg_centroid,
                    avg_rolloff,
                    avg_zcr,
                    avg_high_band,
                    avg_flatness,
                ):
                    _record_tail_rejection("family_mismatch")
                    continue

                score = (
                    candidate_duration * 2.0
                    + max(0.0, lead_centroid - avg_centroid) / 1000.0
                    + max(0.0, lead_rolloff - avg_rolloff) / 1500.0
                    + max(0.0, lead_high_band - avg_high_band) * 4.0
                    - candidate_speech * 0.2
                )

                detection = {
                    "word": estimated_label,
                    "start": round(cand_start, 4),
                    "end": round(cand_end, 4),
                    "duration": round(candidate_duration, 4),
                    "context": f"attached to '{word['word']}'",
                    "detection_source": "audio_attached_tail",
                    "kind": "audio_hesitation",
                    "attachment": "word_offset",
                    "attached_word": word.get("word"),
                    "attached_word_start": word_start,
                    "attached_word_end": word_end,
                    "attached_word_end_frame": word.get("end_frame"),
                    "attached_word_source_end_frame": word.get("source_end_frame"),
                    "selected": True,
                    "boundary_source": "waveform",
                    "next_word": next_word.get("word") if isinstance(next_word, dict) else None,
                    "next_word_start": next_word.get("start") if isinstance(next_word, dict) else None,
                    "next_word_start_frame": next_word.get("start_frame") if isinstance(next_word, dict) else None,
                    "next_word_source_start_frame": next_word.get("source_start_frame") if isinstance(next_word, dict) else None,
                }
                if debug_enabled and word_debug_rejections:
                    detection["debug_nearby_rejections"] = list(word_debug_rejections)
                detection["start_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_start,
                    "start_frame",
                    "end_frame",
                )
                detection["end_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_end,
                    "start_frame",
                    "end_frame",
                )
                detection["source_start_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_start,
                    "source_start_frame",
                    "source_end_frame",
                )
                detection["source_end_frame"] = self._estimate_word_relative_frame(
                    word,
                    cand_end,
                    "source_start_frame",
                    "source_end_frame",
                )

                if score > best_score:
                    best_detection = detection
                    best_score = score

            if best_detection is not None:
                detections.append(best_detection)
            elif debug_enabled and word_debug_rejections:
                logger.debug("tail hesitation miss for '%s': %s", word["word"], ", ".join(word_debug_rejections))

        detections.sort(key=lambda detection: (float(detection["start"]), float(detection["end"])))
        return detections

    @staticmethod
    def _robust_norm(values: np.ndarray) -> np.ndarray:
        low = float(np.percentile(values, 10))
        high = float(np.percentile(values, 90))
        if high - low < 1e-9:
            return np.zeros_like(values, dtype=np.float32)
        return np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)

    @staticmethod
    def _extract_intervals_from_mask(
        mask: np.ndarray,
        times: np.ndarray,
        hop_seconds: float,
        min_frames: int = 1,
    ) -> list[tuple[float, float]]:
        if mask.size == 0 or times.size == 0:
            return []

        intervals: list[tuple[float, float]] = []
        run_start = None
        for idx, flag in enumerate(mask):
            if flag and run_start is None:
                run_start = idx
            elif not flag and run_start is not None:
                if idx - run_start >= min_frames:
                    end_idx = max(run_start, idx - 1)
                    intervals.append((float(times[run_start]), float(times[end_idx] + hop_seconds)))
                run_start = None

        if run_start is not None and len(times) - run_start >= min_frames:
            intervals.append((float(times[run_start]), float(times[-1] + hop_seconds)))

        return intervals

    @staticmethod
    def _select_interval_by_overlap(
        intervals: list[tuple[float, float]],
        seed_start: float,
        seed_end: float,
    ) -> tuple[float, float] | None:
        if not intervals:
            return None

        seed_mid = (seed_start + seed_end) / 2.0
        best_interval = None
        best_score = None
        for interval_start, interval_end in intervals:
            overlap = max(0.0, min(seed_end, interval_end) - max(seed_start, interval_start))
            center_distance = abs(((interval_start + interval_end) / 2.0) - seed_mid)
            duration = max(0.0, interval_end - interval_start)
            score = (overlap, -center_distance, duration)
            if best_score is None or score > best_score:
                best_score = score
                best_interval = (interval_start, interval_end)
        return best_interval

    @staticmethod
    def _merge_nearby_intervals(
        intervals: list[tuple[float, float]],
        max_gap_seconds: float,
    ) -> list[tuple[float, float]]:
        if not intervals:
            return []

        ordered = sorted(intervals, key=lambda interval: (interval[0], interval[1]))
        merged = [ordered[0]]
        for start, end in ordered[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end + max_gap_seconds:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    def _pick_valley_time(
        self,
        profile: dict[str, Any],
        target: float,
        window_start: float,
        window_end: float,
    ) -> float:
        times = profile["times"]
        valley_signal = profile["speech_energy"]
        indices = np.where((times >= window_start) & (times <= window_end))[0]
        if indices.size == 0:
            return target

        local_times = times[indices]
        local_signal = valley_signal[indices]
        span = max(window_end - window_start, 1e-6)
        distance_penalty = np.abs(local_times - target) / span
        scores = local_signal + (distance_penalty * 0.12)
        best_idx = int(indices[int(np.argmin(scores))])
        return float(times[best_idx])

    def _segments_with_boundary_source(self, segments: list[dict], source: str) -> list[dict]:
        updated_segments = []
        for seg in segments:
            updated_segments.append(
                self._annotate_refined_segment(
                    seg,
                    boundary_source=source,
                    refine_reason="seed_fallback" if source != "waveform" else "waveform_refine",
                    used_fallback_seed=source != "waveform",
                )
            )
        return updated_segments

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def _annotate_refined_segment(
        self,
        segment: dict,
        *,
        boundary_source: str | None = None,
        refine_confidence: float | None = None,
        refine_reason: str | None = None,
        used_fallback_seed: bool | None = None,
    ) -> dict:
        updated = dict(segment)
        try:
            updated["duration"] = round(
                max(0.0, float(updated.get("end", 0.0)) - float(updated.get("start", 0.0))),
                4,
            )
        except Exception:
            pass
        if boundary_source is not None:
            updated["boundary_source"] = boundary_source
        updated["refine_confidence"] = (
            round(self._clamp01(refine_confidence), 3)
            if refine_confidence is not None
            else None
        )
        updated["refine_reason"] = str(refine_reason or "unspecified")
        updated["used_fallback_seed"] = bool(used_fallback_seed)
        return updated

    def _measure_silence_valley_confidence(
        self,
        profile: dict[str, Any],
        interval_start: float,
        interval_end: float,
        window_start: float,
        window_end: float,
    ) -> tuple[float, str]:
        times = profile["times"]
        speech_score = profile["speech_score"]
        dbfs = profile["dbfs"]
        valley_idx = np.where((times >= interval_start) & (times <= interval_end))[0]
        if valley_idx.size == 0:
            return 0.0, "missing_valley_window"

        shoulder_span = max(0.12, min(0.28, max(window_end - window_start, 0.12) / 2.0))
        left_idx = np.where((times >= max(window_start, interval_start - shoulder_span)) & (times < interval_start))[0]
        right_idx = np.where((times > interval_end) & (times <= min(window_end, interval_end + shoulder_span)))[0]
        shoulder_idx = np.concatenate([left_idx, right_idx]) if left_idx.size or right_idx.size else np.array([], dtype=int)
        if shoulder_idx.size == 0:
            return 0.0, "missing_shoulders"

        valley_speech_mean = float(np.mean(speech_score[valley_idx]))
        valley_speech_peak = float(np.percentile(speech_score[valley_idx], 85))
        valley_db_mean = float(np.mean(dbfs[valley_idx]))
        shoulder_speech = float(np.percentile(speech_score[shoulder_idx], 70))
        shoulder_db = float(np.percentile(dbfs[shoulder_idx], 70))
        speech_contrast = max(0.0, shoulder_speech - valley_speech_peak)
        db_contrast = max(0.0, shoulder_db - valley_db_mean)

        confidence = self._clamp01(
            0.60 * (speech_contrast / 0.30)
            + 0.25 * (db_contrast / 10.0)
            + 0.15 * self._clamp01((0.45 - valley_speech_mean) / 0.45)
        )
        loud_background = valley_db_mean > -24.0
        elevated_valley_speech = valley_speech_mean > 0.42
        if speech_contrast >= 0.18 and db_contrast >= 4.0 and not loud_background and not elevated_valley_speech:
            return confidence, "validated_dialogue_valley"
        if speech_contrast >= 0.24 and not loud_background and not elevated_valley_speech:
            return confidence, "validated_speech_contrast"
        return confidence, "weak_dialogue_valley"

    def _spoken_refine_defaults(self) -> dict[str, float | int]:
        return {
            "left_search_ms": int(getattr(settings, "spoken_refine_left_search_ms", 220)),
            "right_search_ms": int(getattr(settings, "spoken_refine_right_search_ms", 320)),
            "start_guard_ms": int(getattr(settings, "spoken_refine_start_guard_ms", 12)),
            "end_guard_ms": int(getattr(settings, "spoken_refine_end_guard_ms", 32)),
            "min_duration_ms": int(getattr(settings, "spoken_refine_min_duration_ms", 30)),
            "zero_cross_window_ms": float(getattr(settings, "spoken_refine_zero_cross_window_ms", 2.0)),
            "expand_confidence_threshold": float(getattr(settings, "spoken_refine_expand_confidence_threshold", 0.52)),
            "fallback_confidence_threshold": float(getattr(settings, "spoken_refine_fallback_confidence_threshold", 0.28)),
            "low_confidence_max_expand_ms": int(getattr(settings, "spoken_refine_low_confidence_max_expand_ms", 45)),
        }

    def _spoken_refine_seed_fallback(self, segment: dict, reason: str, confidence: float | None = None) -> dict:
        return self._annotate_refined_segment(
            segment,
            boundary_source="seed",
            refine_confidence=confidence,
            refine_reason=reason,
            used_fallback_seed=True,
        )

    def _spoken_refine_confidence(
        self,
        *,
        seed_start: float,
        seed_end: float,
        component_start: float,
        component_end: float,
        refined_start: float,
        refined_end: float,
        left_bound: float,
        right_bound: float,
        valley_left_score: float,
        valley_right_score: float,
        speech_left_score: float,
        speech_right_score: float,
        local_floor: float,
        local_ceiling: float,
    ) -> float:
        seed_duration = max(seed_end - seed_start, 1e-6)
        component_duration = max(component_end - component_start, 1e-6)
        refined_duration = max(refined_end - refined_start, 1e-6)
        overlap = max(0.0, min(seed_end, component_end) - max(seed_start, component_start))
        overlap_score = self._clamp01(overlap / seed_duration)
        component_score = self._clamp01(overlap / component_duration)
        seed_coverage = self._clamp01(
            max(0.0, min(seed_end, refined_end) - max(seed_start, refined_start)) / seed_duration
        )
        shrink_penalty = self._clamp01(max(0.0, seed_duration - refined_duration) / seed_duration)
        score_span = max(local_ceiling - local_floor, 1e-6)
        onset_contrast = self._clamp01((speech_left_score - valley_left_score) / score_span)
        offset_contrast = self._clamp01((speech_right_score - valley_right_score) / score_span)
        return self._clamp01(
            0.32 * max(overlap_score, component_score)
            + 0.24 * seed_coverage
            + 0.22 * onset_contrast
            + 0.22 * offset_contrast
            - 0.18 * shrink_penalty
        )

    @staticmethod
    def _find_nearest_zero_crossing(
        y: np.ndarray,
        target_sample: int,
        sr: int = 16000,
        window_ms: float = 2.0,
    ) -> int:
        """Return the nearest zero-crossing sample index within +/- window_ms of target_sample."""
        half_window = int(sr * window_ms / 1000.0)
        lo = max(0, target_sample - half_window)
        hi = min(len(y) - 1, target_sample + half_window)
        if lo >= hi:
            return target_sample
        segment = y[lo:hi + 1]
        crossings = np.where(np.diff(np.signbit(segment)))[0]
        if crossings.size == 0:
            return target_sample
        abs_crossings = crossings + lo
        return int(abs_crossings[np.argmin(np.abs(abs_crossings - target_sample))])

    def refine_filler_boundaries_waveform(
        self,
        audio_path: str,
        segments: list[dict],
        sample_rate: int = 16000,
        hop_ms: int = 5,
        search_window_ms: float = 200.0,
        profile: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Refine filler word boundaries using bidirectional energy gradient
        search and zero-crossing snap.  Unlike refine_segments_to_low_energy,
        this can EXPAND cut boundaries to catch the true onset/offset."""
        if not segments:
            return []

        profile = profile or self.build_waveform_profile(audio_path, sample_rate=sample_rate, hop_ms=hop_ms)
        if profile is None:
            return self._segments_with_boundary_source(segments, "fallback")

        try:
            y_raw = load_audio_np(audio_path, sr=sample_rate)
        except Exception:
            y_raw = None

        times = profile["times"]
        rms = profile["rms"]
        duration = float(profile["duration"])
        hop_seconds = float(profile["hop_seconds"])

        # Smooth RMS for onset/offset detection
        smooth_k = max(3, int(round(0.015 / max(hop_seconds, 1e-6))))
        kernel = np.ones(smooth_k, dtype=np.float32) / float(smooth_k)
        rms_smooth = np.convolve(rms, kernel, mode="same").astype(np.float32)

        search_window_s = search_window_ms / 1000.0
        refined: list[dict] = []

        for seg in segments:
            try:
                start = max(0.0, min(duration, float(seg["start"])))
                end = max(0.0, min(duration, float(seg["end"])))
            except Exception:
                refined.append(dict(seg))
                continue
            if end <= start:
                refined.append(dict(seg))
                continue

            prev_word_end = None
            next_word_start = None
            try:
                if seg.get("prev_word_end") is not None:
                    prev_word_end = max(0.0, min(duration, float(seg["prev_word_end"])))
            except Exception:
                pass
            try:
                if seg.get("next_word_start") is not None:
                    next_word_start = max(0.0, min(duration, float(seg["next_word_start"])))
            except Exception:
                pass

            # --- Stage 1a: Find true onset (backward search via RMS threshold) ---
            backward_limit = max(0.0, start - search_window_s)
            if prev_word_end is not None:
                backward_limit = max(backward_limit, prev_word_end)

            onset_search = np.where((times >= backward_limit) & (times <= start))[0]
            new_start = start
            if onset_search.size >= 3:
                local_rms = rms_smooth[onset_search]
                # Use quietest 30% of the search window as noise reference
                noise_ref = float(np.percentile(local_rms, 30))
                # Onset = where energy first rises meaningfully above noise
                onset_threshold = max(noise_ref * 2.5, noise_ref + 0.003)
                above = np.where(local_rms > onset_threshold)[0]
                if above.size > 0:
                    onset_frame = onset_search[above[0]]
                    candidate = float(times[onset_frame])
                    if candidate < start:
                        new_start = candidate

            # --- Stage 1b: Find true offset (forward search via RMS threshold) ---
            forward_limit = min(duration, end + search_window_s)
            if next_word_start is not None:
                forward_limit = min(forward_limit, next_word_start)

            offset_search = np.where((times >= end) & (times <= forward_limit))[0]
            new_end = end
            if offset_search.size >= 3:
                local_rms = rms_smooth[offset_search]
                noise_ref = float(np.percentile(local_rms, 30))
                offset_threshold = max(noise_ref * 2.5, noise_ref + 0.003)
                # Scan backward from end: find last frame still above threshold
                above = np.where(local_rms > offset_threshold)[0]
                if above.size > 0:
                    offset_frame = offset_search[above[-1]]
                    candidate = float(times[offset_frame]) + hop_seconds
                    if candidate > end:
                        new_end = candidate

            # --- Stage 2: Zero-crossing snap ---
            if y_raw is not None and y_raw.size > 0:
                s_sample = max(0, min(len(y_raw) - 1, int(round(new_start * sample_rate))))
                e_sample = max(0, min(len(y_raw) - 1, int(round(new_end * sample_rate))))
                new_start = self._find_nearest_zero_crossing(y_raw, s_sample, sr=sample_rate) / sample_rate
                new_end = self._find_nearest_zero_crossing(y_raw, e_sample, sr=sample_rate) / sample_rate

            # --- Clamp to neighbor word boundaries ---
            if prev_word_end is not None:
                new_start = max(new_start, prev_word_end)
            if next_word_start is not None:
                new_end = min(new_end, next_word_start)
            new_start = max(0.0, new_start)
            new_end = min(duration, new_end)

            if new_end <= new_start:
                refined.append(dict(seg))
                continue

            updated = dict(seg)
            updated["start"] = round(new_start, 4)
            updated["end"] = round(new_end, 4)
            updated["duration"] = round(updated["end"] - updated["start"], 4)
            updated["boundary_source"] = "waveform_gradient"
            refined.append(updated)

        return refined

    def build_waveform_profile(
        self,
        audio_path: str,
        sample_rate: int = 16000,
        hop_ms: int = 5,
    ) -> dict[str, Any] | None:
        try:
            import librosa
        except Exception:
            return None

        try:
            y, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
        except Exception as e:
            logger.warning("Waveform profile build skipped for %s: %s", audio_path, e)
            return None

        if y.size == 0:
            return None

        hop_length, frame_length, n_fft = _stft_window_params(sr, hop_ms)
        S = np.abs(
            librosa.stft(
                y,
                n_fft=n_fft,
                hop_length=hop_length,
                win_length=frame_length,
                center=True,
            )
        )
        if S.size == 0:
            return None

        rms = librosa.feature.rms(S=S, frame_length=n_fft)[0]
        flatness = librosa.feature.spectral_flatness(S=np.maximum(S, 1e-10))[0]
        centroid = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
        rolloff = librosa.feature.spectral_rolloff(S=S, sr=sr)[0]
        zcr = librosa.feature.zero_crossing_rate(
            y,
            frame_length=frame_length,
            hop_length=hop_length,
            center=True,
        )[0]
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        power = S ** 2
        total_power = np.mean(power, axis=0) + 1e-9
        band_mask = (freqs >= 120.0) & (freqs <= 4500.0)
        high_band_mask = (freqs >= 3500.0) & (freqs <= min(float(sr) / 2.0, 9000.0))
        band_power = np.mean(power[band_mask], axis=0) if np.any(band_mask) else total_power
        high_band_power = np.mean(power[high_band_mask], axis=0) if np.any(high_band_mask) else total_power
        band_ratio = band_power / total_power
        high_band_ratio = high_band_power / total_power
        times = librosa.frames_to_time(
            np.arange(S.shape[1]),
            sr=sr,
            hop_length=hop_length,
            n_fft=n_fft,
        )

        frame_count = min(
            len(times),
            len(rms),
            len(flatness),
            len(centroid),
            len(rolloff),
            len(zcr),
            len(band_ratio),
            len(high_band_ratio),
        )
        if frame_count <= 0:
            return None

        times = times[:frame_count].astype(np.float32, copy=False)
        rms = rms[:frame_count].astype(np.float32, copy=False)
        flatness = flatness[:frame_count].astype(np.float32, copy=False)
        centroid = centroid[:frame_count].astype(np.float32, copy=False)
        rolloff = rolloff[:frame_count].astype(np.float32, copy=False)
        zcr = zcr[:frame_count].astype(np.float32, copy=False)
        band_ratio = band_ratio[:frame_count].astype(np.float32, copy=False)
        high_band_ratio = high_band_ratio[:frame_count].astype(np.float32, copy=False)

        speech_score = (
            0.34 * self._robust_norm(rms)
            + 0.18 * self._robust_norm(band_ratio)
            + 0.10 * self._robust_norm(high_band_ratio)
            + 0.15 * (1.0 - self._robust_norm(flatness))
            + 0.11 * (1.0 - self._robust_norm(zcr))
            + 0.07 * (1.0 - self._robust_norm(centroid))
            + 0.05 * (1.0 - self._robust_norm(rolloff))
        ).astype(np.float32)
        speech_energy = (
            0.70 * self._robust_norm(rms)
            + 0.20 * self._robust_norm(band_ratio)
            + 0.10 * self._robust_norm(high_band_ratio)
        ).astype(np.float32)
        smooth_frames = max(3, int(round(0.04 / max(hop_ms / 1000.0, 1e-3))))
        kernel = np.ones(smooth_frames, dtype=np.float32) / float(smooth_frames)
        speech_score = np.convolve(speech_score, kernel, mode="same").astype(np.float32)
        speech_energy = np.convolve(speech_energy, kernel, mode="same").astype(np.float32)
        dbfs = (20.0 * np.log10(np.maximum(rms, 1e-6))).astype(np.float32)

        return {
            "audio_path": audio_path,
            "sample_rate": sr,
            "hop_ms": hop_ms,
            "hop_seconds": hop_ms / 1000.0,
            "duration": len(y) / sr,
            "times": times,
            "rms": rms,
            "flatness": flatness,
            "centroid": centroid,
            "rolloff": rolloff,
            "zcr": zcr,
            "band_ratio": band_ratio,
            "high_band_ratio": high_band_ratio,
            "speech_score": speech_score,
            "speech_energy": speech_energy,
            "dbfs": dbfs,
        }

    def refine_spoken_segments_to_waveform(
        self,
        audio_path: str,
        segments: list[dict],
        *,
        sample_rate: int = 16000,
        hop_ms: int = 5,
        left_search_ms: int | None = None,
        right_search_ms: int | None = None,
        start_guard_ms: int | None = None,
        end_guard_ms: int | None = None,
        min_duration_ms: int | None = None,
        zero_cross_window_ms: float | None = None,
        expand_confidence_threshold: float | None = None,
        fallback_confidence_threshold: float | None = None,
        low_confidence_max_expand_ms: int | None = None,
        profile: dict[str, Any] | None = None,
    ) -> list[dict]:
        if not segments:
            return []

        defaults = self._spoken_refine_defaults()
        left_search_ms = int(left_search_ms if left_search_ms is not None else defaults["left_search_ms"])
        right_search_ms = int(right_search_ms if right_search_ms is not None else defaults["right_search_ms"])
        start_guard_ms = int(start_guard_ms if start_guard_ms is not None else defaults["start_guard_ms"])
        end_guard_ms = int(end_guard_ms if end_guard_ms is not None else defaults["end_guard_ms"])
        min_duration_ms = int(min_duration_ms if min_duration_ms is not None else defaults["min_duration_ms"])
        zero_cross_window_ms = float(
            zero_cross_window_ms if zero_cross_window_ms is not None else defaults["zero_cross_window_ms"]
        )
        expand_confidence_threshold = float(
            expand_confidence_threshold
            if expand_confidence_threshold is not None
            else defaults["expand_confidence_threshold"]
        )
        fallback_confidence_threshold = float(
            fallback_confidence_threshold
            if fallback_confidence_threshold is not None
            else defaults["fallback_confidence_threshold"]
        )
        low_confidence_max_expand_ms = int(
            low_confidence_max_expand_ms
            if low_confidence_max_expand_ms is not None
            else defaults["low_confidence_max_expand_ms"]
        )

        profile = profile or self.build_waveform_profile(audio_path, sample_rate=sample_rate, hop_ms=hop_ms)
        if profile is None:
            return [
                self._spoken_refine_seed_fallback(segment, "waveform_profile_unavailable")
                for segment in segments
            ]

        try:
            y_raw = load_audio_np(audio_path, sr=sample_rate)
        except Exception:
            y_raw = None

        times = profile["times"]
        speech_score = profile["speech_score"]
        speech_energy = profile["speech_energy"]
        dbfs = profile["dbfs"]
        duration = float(profile["duration"])
        hop_seconds = float(profile["hop_seconds"])
        min_duration_seconds = max(0.0, min_duration_ms / 1000.0)
        left_search_seconds = max(0.0, left_search_ms / 1000.0)
        right_search_seconds = max(0.0, right_search_ms / 1000.0)
        low_confidence_expand_seconds = max(0.0, low_confidence_max_expand_ms / 1000.0)
        start_guard_seconds = max(0.0, start_guard_ms / 1000.0)
        end_guard_seconds = max(0.0, end_guard_ms / 1000.0)
        min_frames = max(1, int(round(min_duration_seconds / max(hop_seconds, 1e-6))))
        refined_segments: list[dict] = []

        for seg in segments:
            try:
                seed_start = max(0.0, min(duration, float(seg["start"])))
                seed_end = max(0.0, min(duration, float(seg["end"])))
            except Exception:
                refined_segments.append(self._spoken_refine_seed_fallback(seg, "invalid_seed"))
                continue

            if seed_end <= seed_start:
                refined_segments.append(self._spoken_refine_seed_fallback(seg, "empty_seed"))
                continue

            prev_word_end = None
            next_word_start = None
            try:
                if seg.get("prev_word_end") is not None:
                    prev_word_end = max(0.0, min(duration, float(seg["prev_word_end"])))
            except Exception:
                prev_word_end = None
            try:
                if seg.get("next_word_start") is not None:
                    next_word_start = max(0.0, min(duration, float(seg["next_word_start"])))
            except Exception:
                next_word_start = None

            search_start = max(0.0, seed_start - left_search_seconds)
            search_end = min(duration, seed_end + right_search_seconds)
            if prev_word_end is not None:
                search_start = max(search_start, prev_word_end)
            if next_word_start is not None:
                search_end = min(search_end, next_word_start)
            if search_end <= search_start:
                refined_segments.append(self._spoken_refine_seed_fallback(seg, "search_window_collapsed"))
                continue

            window_idx = np.where((times >= search_start) & (times <= search_end))[0]
            if window_idx.size == 0:
                refined_segments.append(self._spoken_refine_seed_fallback(seg, "search_window_empty"))
                continue

            local_speech = speech_score[window_idx]
            local_dbfs = dbfs[window_idx]
            local_floor = float(np.percentile(local_speech, 20))
            local_ceiling = float(np.percentile(local_speech, 90))
            strict_threshold = max(
                float(np.percentile(local_speech, 58)),
                local_ceiling * 0.70,
            )
            relaxed_threshold = max(
                float(np.percentile(local_speech, 42)),
                strict_threshold * 0.78,
            )
            db_floor = max(-60.0, float(np.percentile(local_dbfs, 28)) - 6.0)
            base_mask = (times >= search_start) & (times <= search_end)
            candidate_masks = [
                base_mask & (speech_score >= strict_threshold) & (dbfs >= db_floor),
                base_mask & (speech_score >= relaxed_threshold) & (dbfs >= db_floor - 5.0),
            ]

            component_interval = None
            component_reason = "component_overlap"
            for candidate_mask in candidate_masks:
                candidate_intervals = self._merge_nearby_intervals(
                    self._extract_intervals_from_mask(
                        candidate_mask,
                        times,
                        hop_seconds,
                        min_frames=max(1, min_frames // 2),
                    ),
                    max_gap_seconds=0.06,
                )
                component_interval = self._select_interval_by_overlap(candidate_intervals, seed_start, seed_end)
                if component_interval is not None:
                    break

            if component_interval is None:
                refined_segments.append(self._spoken_refine_seed_fallback(seg, "weak_acoustic_evidence", 0.0))
                continue

            component_start = max(search_start, float(component_interval[0]))
            component_end = min(search_end, float(component_interval[1]))
            continuation_threshold = max(float(np.percentile(local_speech, 34)), relaxed_threshold * 0.70)
            continuation_mask = base_mask & (speech_score >= continuation_threshold) & (dbfs >= db_floor - 7.0)
            continuation_interval = self._select_interval_by_overlap(
                self._merge_nearby_intervals(
                    self._extract_intervals_from_mask(
                        continuation_mask,
                        times,
                        hop_seconds,
                        min_frames=max(1, min_frames // 3),
                    ),
                    max_gap_seconds=0.08,
                ),
                component_start,
                component_end,
            )
            if continuation_interval is not None:
                continuation_start = max(search_start, float(continuation_interval[0]))
                continuation_end = min(search_end, float(continuation_interval[1]))
                extends_left = continuation_start < (component_start - max(hop_seconds, 0.01))
                extends_right = continuation_end > (component_end + max(hop_seconds, 0.01))
                fully_covers_component = (
                    continuation_start <= component_start + max(hop_seconds, 0.01)
                    and continuation_end >= component_end - max(hop_seconds, 0.01)
                )
                if fully_covers_component and (extends_left or extends_right):
                    component_start = continuation_start
                    component_end = continuation_end
                    component_reason = "continuation_component"

            left_bound = max(search_start, prev_word_end) if prev_word_end is not None else search_start
            left_release_bound = (
                min(seed_end, prev_word_end + start_guard_seconds)
                if prev_word_end is not None
                else left_bound
            )
            right_bound = min(search_end, next_word_start) if next_word_start is not None else search_end
            right_release_bound = right_bound
            if next_word_start is not None:
                right_release_bound = max(component_start + min_duration_seconds, next_word_start - end_guard_seconds)

            start_candidate = self._pick_valley_time(
                profile,
                component_start,
                max(left_bound, component_start - min(left_search_seconds, 0.10)),
                component_start,
            )
            end_candidate = self._pick_valley_time(
                profile,
                component_end,
                component_end,
                min(right_release_bound, component_end + min(right_search_seconds, 0.14)),
            )

            start_candidate = max(left_bound, min(component_start, start_candidate))
            end_candidate = min(right_release_bound, max(component_end, end_candidate))

            valley_left_idx = np.where(
                (times >= max(left_bound, start_candidate - max(hop_seconds, 0.01)))
                & (times <= start_candidate + max(hop_seconds, 0.01))
            )[0]
            valley_right_idx = np.where(
                (times >= max(component_end, end_candidate - max(hop_seconds, 0.01)))
                & (times <= min(right_bound, end_candidate + max(hop_seconds, 0.01)))
            )[0]
            speech_left_idx = np.where(
                (times >= component_start)
                & (times <= min(right_bound, component_start + max(0.03, 3.0 * hop_seconds)))
            )[0]
            speech_right_idx = np.where(
                (times >= max(left_bound, component_end - max(0.03, 3.0 * hop_seconds)))
                & (times <= component_end)
            )[0]

            valley_left_score = float(np.mean(speech_energy[valley_left_idx])) if valley_left_idx.size else float(
                np.mean(speech_energy[window_idx])
            )
            valley_right_score = float(np.mean(speech_energy[valley_right_idx])) if valley_right_idx.size else float(
                np.mean(speech_energy[window_idx])
            )
            speech_left_score = float(np.percentile(speech_energy[speech_left_idx], 75)) if speech_left_idx.size else float(
                np.percentile(speech_energy[window_idx], 75)
            )
            speech_right_score = float(np.percentile(speech_energy[speech_right_idx], 75)) if speech_right_idx.size else float(
                np.percentile(speech_energy[window_idx], 75)
            )

            preliminary_confidence = self._spoken_refine_confidence(
                seed_start=seed_start,
                seed_end=seed_end,
                component_start=component_start,
                component_end=component_end,
                refined_start=start_candidate,
                refined_end=end_candidate,
                left_bound=left_bound,
                right_bound=right_bound,
                valley_left_score=valley_left_score,
                valley_right_score=valley_right_score,
                speech_left_score=speech_left_score,
                speech_right_score=speech_right_score,
                local_floor=local_floor,
                local_ceiling=local_ceiling,
            )

            seed_clamped_start = max(left_bound, seed_start)
            seed_clamped_end = min(right_release_bound, seed_end)
            seed_duration = max(seed_end - seed_start, 1e-6)
            component_overlap = max(0.0, min(seed_end, component_end) - max(seed_start, component_start))
            component_overlap_ratio = self._clamp01(component_overlap / seed_duration)
            refined_duration = max(end_candidate - start_candidate, 1e-6)
            refined_duration_ratio = self._clamp01(refined_duration / seed_duration)
            allow_inward_shrink = (
                preliminary_confidence >= max(0.82, expand_confidence_threshold + 0.20)
                and component_overlap_ratio >= 0.88
                and refined_duration_ratio >= 0.82
            )
            if not allow_inward_shrink and seed_clamped_end > seed_clamped_start:
                preserved_start = max(left_bound, min(start_candidate, seed_clamped_start))
                preserved_end = min(right_release_bound, max(end_candidate, seed_clamped_end))
                if preserved_start != start_candidate or preserved_end != end_candidate:
                    start_candidate = preserved_start
                    end_candidate = preserved_end
                    if component_reason == "continuation_component":
                        component_reason = "seed_preserving_component"

            confidence = self._spoken_refine_confidence(
                seed_start=seed_start,
                seed_end=seed_end,
                component_start=component_start,
                component_end=component_end,
                refined_start=start_candidate,
                refined_end=end_candidate,
                left_bound=left_bound,
                right_bound=right_bound,
                valley_left_score=valley_left_score,
                valley_right_score=valley_right_score,
                speech_left_score=speech_left_score,
                speech_right_score=speech_right_score,
                local_floor=local_floor,
                local_ceiling=local_ceiling,
            )

            if confidence < fallback_confidence_threshold:
                refined_segments.append(
                    self._spoken_refine_seed_fallback(seg, "weak_acoustic_evidence", confidence)
                )
                continue

            refine_reason = component_reason
            if confidence < expand_confidence_threshold:
                start_candidate = max(
                    start_candidate,
                    max(left_release_bound, seed_start - low_confidence_expand_seconds),
                )
                end_candidate = min(end_candidate, seed_end + low_confidence_expand_seconds)
                refine_reason = "low_confidence_capped_expand"

            if end_candidate - start_candidate < min_duration_seconds:
                midpoint = (seed_start + seed_end) / 2.0
                half = min_duration_seconds / 2.0
                start_candidate = max(left_bound, midpoint - half)
                end_candidate = min(right_release_bound, midpoint + half)

            if end_candidate <= start_candidate:
                refined_segments.append(
                    self._spoken_refine_seed_fallback(seg, "collapsed_after_refine", confidence)
                )
                continue

            if y_raw is not None and y_raw.size > 0 and zero_cross_window_ms > 0.0:
                start_sample = max(0, min(len(y_raw) - 1, int(round(start_candidate * sample_rate))))
                end_sample = max(0, min(len(y_raw) - 1, int(round(end_candidate * sample_rate))))
                start_candidate = self._find_nearest_zero_crossing(
                    y_raw,
                    start_sample,
                    sr=sample_rate,
                    window_ms=zero_cross_window_ms,
                ) / sample_rate
                end_candidate = self._find_nearest_zero_crossing(
                    y_raw,
                    end_sample,
                    sr=sample_rate,
                    window_ms=zero_cross_window_ms,
                ) / sample_rate
                start_candidate = max(left_bound, min(component_start, start_candidate))
                end_candidate = min(right_release_bound, max(component_end, end_candidate))

            updated = dict(seg)
            updated["start"] = round(start_candidate, 4)
            updated["end"] = round(end_candidate, 4)
            refined_segments.append(
                self._annotate_refined_segment(
                    updated,
                    boundary_source="waveform",
                    refine_confidence=confidence,
                    refine_reason=refine_reason,
                    used_fallback_seed=False,
                )
            )

        return refined_segments

    def refine_filler_segments_to_waveform(
        self,
        audio_path: str,
        segments: list[dict],
        min_duration_ms: int = 30,
        sample_rate: int = 16000,
        hop_ms: int = 5,
        profile: dict[str, Any] | None = None,
    ) -> list[dict]:
        if not segments:
            return []

        profile = profile or self.build_waveform_profile(audio_path, sample_rate=sample_rate, hop_ms=hop_ms)
        fallback = self._segments_with_boundary_source(
            self.refine_filler_boundaries_waveform(
                audio_path,
                segments,
                sample_rate=sample_rate,
                hop_ms=hop_ms,
                profile=profile,
            ),
            "fallback",
        )
        if profile is None:
            return fallback

        # Load raw waveform for zero-crossing snap
        try:
            _y_raw = load_audio_np(audio_path, sr=sample_rate)
        except Exception:
            _y_raw = None

        times = profile["times"]
        speech_score = profile["speech_score"]
        rms = profile["rms"]
        dbfs = profile["dbfs"]
        flatness = profile["flatness"]
        centroid = profile["centroid"]
        rolloff = profile["rolloff"]
        zcr = profile["zcr"]
        duration = float(profile["duration"])
        hop_seconds = float(profile["hop_seconds"])
        min_frames = max(1, int(round((min_duration_ms / 1000.0) / max(hop_seconds, 1e-6))))

        # Smoothed RMS for onset/offset correction
        _smooth_k = max(3, int(round(0.015 / max(hop_seconds, 1e-6))))
        _rms_smooth = np.convolve(rms, np.ones(_smooth_k, dtype=np.float32) / float(_smooth_k), mode="same")

        refined_segments: list[dict] = []

        for idx, seg in enumerate(segments):
            fallback_seg = fallback[idx] if idx < len(fallback) else dict(seg)
            try:
                start = max(0.0, min(duration, float(seg["start"])))
                end = max(0.0, min(duration, float(seg["end"])))
            except Exception:
                refined_segments.append(fallback_seg)
                continue

            if end <= start:
                refined_segments.append(fallback_seg)
                continue

            prev_word_end = None
            next_word_start = None
            try:
                if seg.get("prev_word_end") is not None:
                    prev_word_end = max(0.0, min(duration, float(seg["prev_word_end"])))
            except Exception:
                prev_word_end = None
            try:
                if seg.get("next_word_start") is not None:
                    next_word_start = max(0.0, min(duration, float(seg["next_word_start"])))
            except Exception:
                next_word_start = None

            search_start = max(0.0, start - 0.20)
            search_end = min(duration, end + 0.20)
            if prev_word_end is not None:
                search_start = max(search_start, prev_word_end)
            if next_word_start is not None:
                search_end = min(search_end, next_word_start)

            if search_end <= search_start:
                refined_segments.append(fallback_seg)
                continue

            window_idx = np.where((times >= search_start) & (times <= search_end))[0]
            if window_idx.size == 0:
                refined_segments.append(fallback_seg)
                continue

            local_speech = speech_score[window_idx]
            local_dbfs = dbfs[window_idx]
            strict_threshold = max(
                float(np.percentile(local_speech, 55)),
                float(np.percentile(local_speech, 70)) * 0.78,
            )
            relaxed_threshold = max(
                float(np.percentile(local_speech, 45)),
                float(np.percentile(local_speech, 60)) * 0.72,
            )
            strict_db_floor = max(-60.0, float(np.percentile(local_dbfs, 30)) - 8.0)
            relaxed_db_floor = max(-62.0, strict_db_floor - 4.0)

            base_mask = (times >= search_start) & (times <= search_end)
            candidate_masks = [
                base_mask
                & (speech_score >= strict_threshold)
                & (dbfs >= strict_db_floor)
                & (flatness <= 0.60)
                & (zcr <= 0.30)
                & (centroid <= 2800.0)
                & (rolloff <= 5000.0),
                base_mask
                & (speech_score >= relaxed_threshold)
                & (dbfs >= relaxed_db_floor),
            ]

            best_interval = None
            for candidate_mask in candidate_masks:
                candidate_intervals = self._merge_nearby_intervals(
                    self._extract_intervals_from_mask(
                        candidate_mask,
                        times,
                        hop_seconds,
                        min_frames=min_frames,
                    ),
                    max_gap_seconds=0.08,
                )
                best_interval = self._select_interval_by_overlap(candidate_intervals, start, end)
                if best_interval is not None:
                    break

            if best_interval is None:
                refined_segments.append(fallback_seg)
                continue

            component_start = max(search_start, float(best_interval[0]))
            component_end = min(search_end, float(best_interval[1]))
            if component_end <= component_start:
                refined_segments.append(fallback_seg)
                continue

            continuation_threshold = max(
                float(np.percentile(local_speech, 35)),
                relaxed_threshold * 0.65,
            )
            continuation_db_floor = relaxed_db_floor - 6.0
            continuation_mask = (
                base_mask
                & (speech_score >= continuation_threshold)
                & (dbfs >= continuation_db_floor)
            )
            continuation_intervals = self._merge_nearby_intervals(
                self._extract_intervals_from_mask(
                    continuation_mask,
                    times,
                    hop_seconds,
                    min_frames=max(1, min_frames // 2),
                ),
                max_gap_seconds=0.08,
            )
            continuation_interval = self._select_interval_by_overlap(
                continuation_intervals,
                component_start,
                component_end,
            )
            if continuation_interval is not None:
                component_start = max(search_start, float(continuation_interval[0]))
                component_end = min(search_end, float(continuation_interval[1]))

            left_bound = max(search_start, prev_word_end) if prev_word_end is not None else search_start
            right_bound = min(search_end, next_word_start) if next_word_start is not None else search_end

            # --- Direct RMS onset correction ---
            # The spectral threshold often starts late for soft filler onsets.
            # Search backward from component_start to find where energy first
            # rises above the local noise floor.
            onset_window = np.where((times >= left_bound) & (times < component_start))[0]
            if onset_window.size >= 3:
                local_rms = _rms_smooth[onset_window]
                noise_ref = float(np.percentile(local_rms, 30))
                onset_thresh = max(noise_ref * 2.5, noise_ref + 0.003)
                above = np.where(local_rms > onset_thresh)[0]
                if above.size > 0:
                    candidate = float(times[onset_window[above[0]]])
                    if candidate < component_start:
                        component_start = max(left_bound, candidate)

            # --- Direct RMS offset correction ---
            offset_window = np.where((times > component_end) & (times <= right_bound))[0]
            if offset_window.size >= 3:
                local_rms = _rms_smooth[offset_window]
                noise_ref = float(np.percentile(local_rms, 30))
                offset_thresh = max(noise_ref * 2.5, noise_ref + 0.003)
                above = np.where(local_rms > offset_thresh)[0]
                if above.size > 0:
                    candidate = float(times[offset_window[above[-1]]]) + hop_seconds
                    if candidate > component_end:
                        component_end = min(right_bound, candidate)

            snapped_start = self._pick_valley_time(
                profile,
                component_start,
                max(left_bound, component_start - 0.06),
                component_start,
            )
            snapped_end = self._pick_valley_time(
                profile,
                component_end,
                component_end,
                min(right_bound, component_end + 0.06),
            )
            snapped_start = max(left_bound, min(component_start, snapped_start))
            snapped_end = min(right_bound, max(component_end, snapped_end))

            # Zero-crossing snap for click-free cuts
            if _y_raw is not None and _y_raw.size > 0:
                s_sample = max(0, min(len(_y_raw) - 1, int(round(snapped_start * sample_rate))))
                e_sample = max(0, min(len(_y_raw) - 1, int(round(snapped_end * sample_rate))))
                snapped_start = self._find_nearest_zero_crossing(_y_raw, s_sample, sr=sample_rate) / sample_rate
                snapped_end = self._find_nearest_zero_crossing(_y_raw, e_sample, sr=sample_rate) / sample_rate
                # Re-clamp after zero-crossing snap
                snapped_start = max(left_bound, snapped_start)
                snapped_end = min(right_bound, snapped_end)

            # Refinement can expand the seed but should never trim inside the
            # original filler span once neighbor-word bounds are applied.
            seed_start = max(left_bound, start)
            seed_end = min(right_bound, end)
            if seed_end > seed_start:
                snapped_start = max(left_bound, min(snapped_start, seed_start))
                snapped_end = min(right_bound, max(snapped_end, seed_end))

            if snapped_end - snapped_start < (min_duration_ms / 1000.0):
                refined_segments.append(fallback_seg)
                continue

            updated = dict(seg)
            updated["start"] = round(snapped_start, 4)
            updated["end"] = round(snapped_end, 4)
            refined_segments.append(
                self._annotate_refined_segment(
                    updated,
                    boundary_source="waveform",
                    refine_reason="filler_waveform_component",
                    used_fallback_seed=False,
                )
            )

        return refined_segments

    def merge_filler_detections(
        self,
        detections: list[dict],
        overlap_tolerance_seconds: float = 0.03,
    ) -> list[dict]:
        valid_detections = []
        for detection in detections:
            try:
                start = float(detection.get("start", 0.0))
                end = float(detection.get("end", start))
            except Exception:
                continue
            if end <= start:
                continue
            valid_detections.append(dict(detection))

        if not valid_detections:
            return []

        valid_detections.sort(key=lambda detection: (float(detection["start"]), float(detection["end"])))
        groups: list[list[dict]] = [[valid_detections[0]]]
        group_start = float(valid_detections[0]["start"])
        group_end = float(valid_detections[0]["end"])
        for detection in valid_detections[1:]:
            start = float(detection["start"])
            end = float(detection["end"])
            overlap = min(group_end, end) - max(group_start, start)
            if overlap >= overlap_tolerance_seconds:
                groups[-1].append(detection)
                group_start = min(group_start, start)
                group_end = max(group_end, end)
            else:
                groups.append([detection])
                group_start = start
                group_end = end

        merged: list[dict] = []
        for group in groups:
            def _priority(item: dict) -> tuple[int, int, float]:
                is_transcript_detection = item.get("kind") != "audio_hesitation" and item.get("word") != "[filler]"
                has_context = 1 if item.get("context") else 0
                duration = max(0.0, float(item.get("end", 0.0)) - float(item.get("start", 0.0)))
                return (1 if is_transcript_detection else 0, has_context, duration)

            primary = max(group, key=_priority)
            merged_detection = dict(primary)
            merged_detection["start"] = round(min(float(item["start"]) for item in group), 4)
            merged_detection["end"] = round(max(float(item["end"]) for item in group), 4)
            merged_detection["duration"] = round(merged_detection["end"] - merged_detection["start"], 4)
            merged_detection["selected"] = any(bool(item.get("selected", True)) for item in group)
            merged_detection["boundary_source"] = (
                "waveform" if any(item.get("boundary_source") == "waveform" for item in group) else "fallback"
            )

            for key in (
                "prev_word",
                "prev_word_end",
                "prev_word_end_frame",
                "prev_word_source_end_frame",
                "next_word",
                "next_word_start",
                "next_word_start_frame",
                "next_word_source_start_frame",
                "attachment",
                "attached_word",
                "attached_word_start",
                "attached_word_end",
                "attached_word_start_frame",
                "attached_word_source_start_frame",
                "source_start_frame",
                "source_end_frame",
            ):
                if merged_detection.get(key) is not None:
                    continue
                for item in group:
                    if item.get(key) is not None:
                        merged_detection[key] = item.get(key)
                        break

            merged.append(merged_detection)

        return merged

    def analyze_filler_candidates(
        self,
        audio_path: str,
        transcript: dict,
        filler_words: list[str] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> list[dict]:
        profile = profile or self.build_waveform_profile(audio_path)
        audio_duration = profile["duration"] if profile else None
        logger.info(
            "analyze_filler_candidates: audio=%s, words=%d, audio_duration=%.2fs",
            audio_path,
            len(transcript.get("words", [])),
            audio_duration or 0.0,
        )
        transcript_fillers = self.detect_filler_words(transcript, filler_words)
        audio_hesitations = self.detect_hesitation_audio_candidates(
            audio_path,
            transcript,
            filler_words=filler_words,
        )
        attached_hesitations = self.detect_attached_hesitation_onsets(
            transcript,
            filler_words=filler_words,
            profile=profile,
        )
        attached_tail_hesitations = self.detect_attached_hesitation_offsets(
            transcript,
            filler_words=filler_words,
            profile=profile,
        )
        logger.info(
            "analyze_filler_candidates: transcript_fillers=%d, audio_hesitations=%d, attached_hesitations=%d, attached_tail_hesitations=%d",
            len(transcript_fillers),
            len(audio_hesitations),
            len(attached_hesitations),
            len(attached_tail_hesitations),
        )
        refined_fillers = self.refine_filler_segments_to_waveform(
            audio_path,
            transcript_fillers,
            profile=profile,
        )
        refined_hesitations = self.refine_filler_segments_to_waveform(
            audio_path,
            audio_hesitations,
            profile=profile,
        )
        return self.merge_filler_detections([
            *refined_fillers,
            *refined_hesitations,
            *attached_hesitations,
            *attached_tail_hesitations,
        ])

    def refine_silence_segments_to_waveform(
        self,
        audio_path: str,
        segments: list[dict],
        preserve_padding_ms: int | None = None,
        search_pad_ms: int = 150,
        min_duration_ms: int = 20,
        min_removable_ms: int = 40,
        natural_pause_floor_ms: int = 60,
        sample_rate: int = 16000,
        hop_ms: int = 5,
        require_dialogue_valley: bool = False,
        profile: dict[str, Any] | None = None,
    ) -> list[dict]:
        if not segments:
            return []

        profile = profile or self.build_waveform_profile(audio_path, sample_rate=sample_rate, hop_ms=hop_ms)
        if profile is None:
            return self._segments_with_boundary_source(segments, "fallback")

        times = profile["times"]
        speech_score = profile["speech_score"]
        dbfs = profile["dbfs"]
        duration = float(profile["duration"])
        hop_seconds = float(profile["hop_seconds"])
        min_frames = max(1, int(round((min_duration_ms / 1000.0) / max(hop_seconds, 1e-6))))
        search_pad_seconds = max(0.0, search_pad_ms / 1000.0)
        refined_segments: list[dict] = []

        for seg in segments:
            try:
                start = max(0.0, min(duration, float(seg["start"])))
                end = max(0.0, min(duration, float(seg["end"])))
            except Exception:
                continue

            if end <= start:
                continue

            window_start = max(0.0, start - search_pad_seconds)
            window_end = min(duration, end + search_pad_seconds)
            window_idx = np.where((times >= window_start) & (times <= window_end))[0]
            if window_idx.size == 0:
                if require_dialogue_valley:
                    continue
                refined_segments.append(self._annotate_refined_segment(
                    seg,
                    boundary_source="fallback",
                    refine_reason="silence_window_empty",
                    used_fallback_seed=True,
                ))
                continue

            local_speech = speech_score[window_idx]
            local_dbfs = dbfs[window_idx]
            strict_threshold = min(
                float(np.percentile(local_speech, 42)),
                float(np.percentile(local_speech, 55)) * 0.86,
            )
            relaxed_threshold = min(
                float(np.percentile(local_speech, 50)),
                float(np.percentile(local_speech, 65)) * 0.92,
            )
            strict_db_ceiling = min(
                float(np.percentile(local_dbfs, 45)) + 3.0,
                float(np.percentile(local_dbfs, 60)),
            )

            base_mask = (times >= window_start) & (times <= window_end)
            candidate_masks = [
                base_mask & (speech_score <= strict_threshold) & (dbfs <= strict_db_ceiling),
                base_mask & (speech_score <= relaxed_threshold),
            ]

            best_interval = None
            for candidate_mask in candidate_masks:
                candidate_intervals = self._extract_intervals_from_mask(
                    candidate_mask,
                    times,
                    hop_seconds,
                    min_frames=min_frames,
                )
                best_interval = self._select_interval_by_overlap(candidate_intervals, start, end)
                if best_interval is not None:
                    break

            if best_interval is None:
                if require_dialogue_valley:
                    continue
                refined_segments.append(self._annotate_refined_segment(
                    seg,
                    boundary_source="fallback",
                    refine_reason="silence_overlap_missing",
                    used_fallback_seed=True,
                ))
                continue

            refined_start = max(window_start, float(best_interval[0]))
            refined_end = min(window_end, float(best_interval[1]))
            if refined_end - refined_start < (min_duration_ms / 1000.0):
                if require_dialogue_valley:
                    continue
                refined_segments.append(self._annotate_refined_segment(
                    seg,
                    boundary_source="fallback",
                    refine_reason="silence_too_short",
                    used_fallback_seed=True,
                ))
                continue

            silence_confidence = None
            silence_reason = "silence_waveform_valley"
            if require_dialogue_valley:
                silence_confidence, silence_reason = self._measure_silence_valley_confidence(
                    profile,
                    refined_start,
                    refined_end,
                    window_start,
                    window_end,
                )
                if silence_reason == "weak_dialogue_valley":
                    continue

            kind = str(seg.get("kind") or "silence")
            output_start = refined_start
            output_end = refined_end
            if preserve_padding_ms is not None:
                effective_padding_ms = max(0, int(preserve_padding_ms))
                if kind == "silence":
                    effective_padding_ms = max(effective_padding_ms, int(natural_pause_floor_ms))
                padding_seconds = effective_padding_ms / 1000.0
                output_start = refined_start + padding_seconds
                output_end = refined_end - padding_seconds
                if output_end - output_start < (min_removable_ms / 1000.0):
                    continue

            updated = dict(seg)
            updated["kind"] = kind
            updated["start"] = round(output_start, 4)
            updated["end"] = round(output_end, 4)
            refined_segments.append(
                self._annotate_refined_segment(
                    updated,
                    boundary_source="waveform",
                    refine_confidence=silence_confidence,
                    refine_reason=silence_reason,
                    used_fallback_seed=False,
                )
            )

        return refined_segments

    def analyze_silence_candidates(
        self,
        audio_path: str,
        threshold_db: int = -40,
        min_duration_ms: int = 300,
        require_dialogue_valley: bool = False,
        profile: dict[str, Any] | None = None,
    ) -> list[dict]:
        profile = profile or self.build_waveform_profile(audio_path)
        silences = self.detect_silence(
            audio_path,
            threshold_db=threshold_db,
            min_duration_ms=min_duration_ms,
        )
        silences = self.refine_silence_segments_to_waveform(
            audio_path,
            silences,
            preserve_padding_ms=None,
            min_duration_ms=max(20, int(min_duration_ms * 0.25)),
            require_dialogue_valley=require_dialogue_valley,
            profile=profile,
        )
        quiet_speech = self.detect_low_prominence_speech(
            audio_path,
            threshold_db=threshold_db,
            min_duration_ms=max(250, int(min_duration_ms * 0.85)),
            quiet_delta_db=12.0,
        )
        quiet_speech = self.refine_segments_to_low_energy(
            audio_path,
            quiet_speech,
            max_shift_ms=40,
            min_duration_ms=max(120, int(min_duration_ms * 0.5)),
            profile=profile,
        )
        quiet_speech = [
            self._annotate_refined_segment(
                segment,
                boundary_source=str(segment.get("boundary_source") or "waveform"),
                refine_confidence=segment.get("refine_confidence"),
                refine_reason=str(segment.get("refine_reason") or "quiet_speech_low_energy"),
                used_fallback_seed=bool(segment.get("used_fallback_seed", False)),
            )
            for segment in quiet_speech
        ]
        return [*silences, *quiet_speech]

    def refine_segments_to_low_energy(
        self,
        audio_path: str,
        segments: list[dict],
        max_shift_ms: int = 80,
        min_duration_ms: int = 20,
        sample_rate: int = 16000,
        hop_ms: int = 10,
        profile: dict[str, Any] | None = None,
    ) -> list[dict]:
        """
        Refine cut boundaries by snapping them inward to nearby low-energy valleys.
        This is conservative by design: it only shrinks cuts, which reduces over-cutting.
        """
        if not segments:
            return []

        profile = profile or self.build_waveform_profile(audio_path, sample_rate=sample_rate, hop_ms=hop_ms)
        if profile is None:
            return [
                self._annotate_refined_segment(
                    segment,
                    boundary_source="seed",
                    refine_reason="waveform_profile_unavailable",
                    used_fallback_seed=True,
                )
                for segment in segments
            ]

        speech_energy = profile["speech_energy"]
        times = profile["times"]
        duration = float(profile["duration"])
        min_duration = max(0.0, min_duration_ms / 1000.0)
        max_shift = max(0.0, max_shift_ms / 1000.0)
        energy_span = max(float(np.max(speech_energy) - np.min(speech_energy)), 1e-9)

        def _pick_valley(target: float, window_start: float, window_end: float) -> float:
            indices = np.where((times >= window_start) & (times <= window_end))[0]
            if indices.size == 0:
                return target

            local_times = times[indices]
            local_energy = speech_energy[indices]
            # Bias toward staying close to the target so we only make local corrections.
            distance_penalty = np.abs(local_times - target) / max(window_end - window_start, 1e-6)
            scores = local_energy + (distance_penalty * 0.15 * energy_span)
            best_idx = int(indices[int(np.argmin(scores))])
            return float(times[best_idx])

        refined = []
        for seg in segments:
            try:
                start = max(0.0, float(seg["start"]))
                end = min(duration, float(seg["end"]))
            except Exception:
                refined.append(self._annotate_refined_segment(
                    seg,
                    boundary_source="seed",
                    refine_reason="invalid_seed",
                    used_fallback_seed=True,
                ))
                continue

            if end <= start:
                refined.append(self._annotate_refined_segment(
                    seg,
                    boundary_source="seed",
                    refine_reason="empty_seed",
                    used_fallback_seed=True,
                ))
                continue

            prev_word_end = None
            next_word_start = None
            try:
                if seg.get("prev_word_end") is not None:
                    prev_word_end = max(0.0, min(duration, float(seg["prev_word_end"])))
            except Exception:
                prev_word_end = None
            try:
                if seg.get("next_word_start") is not None:
                    next_word_start = max(0.0, min(duration, float(seg["next_word_start"])))
            except Exception:
                next_word_start = None

            start_window_start = start
            start_window_end = min(duration, start + max_shift)
            if prev_word_end is not None and prev_word_end < end:
                start_window_start = max(start, prev_word_end)
                start_window_end = max(start_window_start, min(duration, start + max_shift))

            end_target = end
            end_window_end = end
            if next_word_start is not None and next_word_start > start:
                # For filler words, bias the release boundary toward the quietest
                # point before the next spoken word rather than trusting the raw
                # transcript end time alone.
                end_target = min(duration, next_word_start)
                end_window_end = max(start, end_target)

            end_window_start = max(start, end_target - max_shift)

            new_start = _pick_valley(start, start_window_start, start_window_end)
            new_end = _pick_valley(end_target, end_window_start, end_window_end)

            if new_end - new_start < min_duration:
                midpoint = (start + end) / 2.0
                half = min_duration / 2.0
                new_start = max(start, midpoint - half)
                new_end = min(end, midpoint + half)

            if new_end <= new_start:
                refined.append(self._annotate_refined_segment(
                    seg,
                    boundary_source="seed",
                    refine_reason="collapsed_after_refine",
                    used_fallback_seed=True,
                ))
                continue

            updated = dict(seg)
            updated["start"] = round(max(start, new_start), 4)
            updated["end"] = round(min(end, new_end), 4)
            refined.append(
                self._annotate_refined_segment(
                    updated,
                    boundary_source="waveform",
                    refine_reason="local_low_energy_trim",
                    used_fallback_seed=False,
                )
            )

        return refined

    def refine_silence_segments_to_speech_boundaries(
        self,
        audio_path: str,
        segments: list[dict],
        max_shift_ms: int = 200,
        min_duration_ms: int = 20,
        sample_rate: int = 16000,
        hop_ms: int = 10,
    ) -> list[dict]:
        """
        Refine silence regions by finding the strongest overlapping non-speech
        interval in a local speech-activity profile.
        """
        if not segments:
            return []

        try:
            import librosa
        except Exception:
            return segments

        try:
            y, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
        except Exception as e:
            logger.warning("Speech-boundary refinement skipped for %s: %s", audio_path, e)
            return segments

        if y.size == 0:
            return segments

        hop_length, frame_length, n_fft = _stft_window_params(sr, hop_ms)
        S = np.abs(
            librosa.stft(
                y,
                n_fft=n_fft,
                hop_length=hop_length,
                win_length=frame_length,
                center=True,
            )
        )
        if S.size == 0:
            return segments

        rms = librosa.feature.rms(S=S, frame_length=n_fft)[0]
        flatness = librosa.feature.spectral_flatness(S=np.maximum(S, 1e-10))[0]
        rolloff = librosa.feature.spectral_rolloff(S=S, sr=sr)[0]
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        band_mask = (freqs >= 120.0) & (freqs <= 4500.0)
        high_band_mask = (freqs >= 3500.0) & (freqs <= min(float(sr) / 2.0, 9000.0))
        power = S ** 2
        total_power = np.mean(power, axis=0) + 1e-9
        band_power = np.mean(power[band_mask], axis=0) if np.any(band_mask) else total_power
        band_ratio = band_power / total_power
        high_band_power = np.mean(power[high_band_mask], axis=0) if np.any(high_band_mask) else total_power
        high_band_ratio = high_band_power / total_power
        times = librosa.frames_to_time(np.arange(S.shape[1]), sr=sr, hop_length=hop_length, n_fft=n_fft)

        def _robust_norm(values: np.ndarray) -> np.ndarray:
            low = float(np.percentile(values, 10))
            high = float(np.percentile(values, 90))
            if high - low < 1e-9:
                return np.zeros_like(values, dtype=np.float32)
            return np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)

        speech_score = (
            0.35 * _robust_norm(rms)
            + 0.20 * _robust_norm(band_ratio)
            + 0.20 * _robust_norm(high_band_ratio)
            + 0.10 * _robust_norm(rolloff)
            + 0.15 * (1.0 - _robust_norm(flatness))
        )
        smooth_frames = max(3, int(round(0.05 / max(hop_ms / 1000.0, 1e-3))))
        kernel = np.ones(smooth_frames, dtype=np.float32) / float(smooth_frames)
        speech_score = np.convolve(speech_score, kernel, mode="same")

        min_frames = max(1, int(round((min_duration_ms / 1000.0) / max(hop_ms / 1000.0, 1e-3))))
        max_shift = max(0.0, max_shift_ms / 1000.0)

        def _extract_intervals(mask: np.ndarray, window_times: np.ndarray) -> list[tuple[float, float]]:
            if mask.size == 0:
                return []
            intervals: list[tuple[float, float]] = []
            run_start = None
            for idx, flag in enumerate(mask):
                if flag and run_start is None:
                    run_start = idx
                elif not flag and run_start is not None:
                    if idx - run_start >= min_frames:
                        end_idx = min(len(window_times) - 1, idx - 1)
                        end_time = float(window_times[end_idx] + (hop_ms / 1000.0))
                        intervals.append((float(window_times[run_start]), end_time))
                    run_start = None
            if run_start is not None and len(window_times) - run_start >= min_frames:
                end_time = float(window_times[-1] + (hop_ms / 1000.0))
                intervals.append((float(window_times[run_start]), end_time))
            return intervals

        refined = []
        for seg in segments:
            try:
                start = float(seg["start"])
                end = float(seg["end"])
            except Exception:
                refined.append(seg)
                continue

            if end <= start:
                refined.append(seg)
                continue

            window_start = max(0.0, start - max_shift)
            window_end = min(float(times[-1]) + (hop_ms / 1000.0), end + max_shift)
            idx = np.where((times >= window_start) & (times <= window_end))[0]
            if idx.size == 0:
                refined.append(seg)
                continue

            local_times = times[idx]
            local_scores = speech_score[idx]
            q25 = float(np.percentile(local_scores, 25))
            q75 = float(np.percentile(local_scores, 75))
            threshold = q25 + ((q75 - q25) * 0.25)
            non_speech_mask = local_scores <= threshold
            candidate_intervals = _extract_intervals(non_speech_mask, local_times)
            if not candidate_intervals:
                refined.append(seg)
                continue

            best_interval = None
            best_score = None
            seg_mid = (start + end) / 2.0
            for cand_start, cand_end in candidate_intervals:
                overlap = max(0.0, min(end, cand_end) - max(start, cand_start))
                center_distance = abs(((cand_start + cand_end) / 2.0) - seg_mid)
                score = (overlap, -center_distance)
                if best_score is None or score > best_score:
                    best_score = score
                    best_interval = (cand_start, cand_end)

            if best_interval is None:
                refined.append(seg)
                continue

            cand_start, cand_end = best_interval
            new_start = max(start, cand_start)
            new_end = min(end, cand_end)

            # Zero-crossing snap for click-free silence boundaries
            s_sample = max(0, min(len(y) - 1, int(round(new_start * sr))))
            e_sample = max(0, min(len(y) - 1, int(round(new_end * sr))))
            new_start = self._find_nearest_zero_crossing(y, s_sample, sr=sr) / sr
            new_end = self._find_nearest_zero_crossing(y, e_sample, sr=sr) / sr

            if new_end - new_start < (min_duration_ms / 1000.0):
                refined.append(seg)
                continue

            updated = dict(seg)
            updated["start"] = round(new_start, 4)
            updated["end"] = round(new_end, 4)
            updated["duration"] = round(updated["end"] - updated["start"], 4)
            refined.append(updated)

        return refined

    def detect_low_prominence_speech(
        self,
        audio_path: str,
        threshold_db: int = -40,
        min_duration_ms: int = 250,
        quiet_delta_db: float = 12.0,
        sample_rate: int = 16000,
        hop_ms: int = 20,
    ) -> list[dict]:
        """
        Find speech-like regions that are far quieter than the dominant speaker.
        This is designed for off-camera interviewer questions or room bleed that
        still transcribes as speech but should often behave more like removable
        background audio than primary dialogue.
        """
        try:
            import librosa
        except Exception:
            return []

        try:
            y, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
        except Exception as e:
            logger.warning("Low-prominence speech analysis skipped for %s: %s", audio_path, e)
            return []

        if y.size == 0:
            return []

        hop_length, frame_length, n_fft = _stft_window_params(sr, hop_ms)
        S = np.abs(
            librosa.stft(
                y,
                n_fft=n_fft,
                hop_length=hop_length,
                win_length=frame_length,
                center=True,
            )
        )
        if S.size == 0:
            return []

        rms = librosa.feature.rms(S=S, frame_length=n_fft)[0]
        if rms.size == 0:
            return []

        flatness = librosa.feature.spectral_flatness(S=np.maximum(S, 1e-10))[0]
        rolloff = librosa.feature.spectral_rolloff(S=S, sr=sr)[0]
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        band_mask = (freqs >= 120.0) & (freqs <= 4500.0)
        high_band_mask = (freqs >= 3500.0) & (freqs <= min(float(sr) / 2.0, 9000.0))
        power = S ** 2
        total_power = np.mean(power, axis=0) + 1e-9
        band_power = np.mean(power[band_mask], axis=0) if np.any(band_mask) else total_power
        high_band_power = np.mean(power[high_band_mask], axis=0) if np.any(high_band_mask) else total_power
        band_ratio = band_power / total_power
        high_band_ratio = high_band_power / total_power
        times = librosa.frames_to_time(
            np.arange(S.shape[1]),
            sr=sr,
            hop_length=hop_length,
            n_fft=n_fft,
        )

        def _robust_norm(values: np.ndarray) -> np.ndarray:
            low = float(np.percentile(values, 10))
            high = float(np.percentile(values, 90))
            if high - low < 1e-9:
                return np.zeros_like(values, dtype=np.float32)
            return np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)

        speech_score = (
            0.35 * _robust_norm(rms)
            + 0.20 * _robust_norm(band_ratio)
            + 0.20 * _robust_norm(high_band_ratio)
            + 0.10 * _robust_norm(rolloff)
            + 0.15 * (1.0 - _robust_norm(flatness))
        )
        smooth_frames = max(3, int(round(0.06 / max(hop_ms / 1000.0, 1e-3))))
        kernel = np.ones(smooth_frames, dtype=np.float32) / float(smooth_frames)
        speech_score = np.convolve(speech_score, kernel, mode="same")

        speech_threshold = float(np.percentile(speech_score, 58))
        speech_threshold = max(speech_threshold, float(np.percentile(speech_score, 40)))
        active_mask = speech_score >= speech_threshold
        if not np.any(active_mask):
            return []

        dbfs = 20.0 * np.log10(np.maximum(rms, 1e-6))
        dominant_db = float(np.percentile(dbfs[active_mask], 75))
        quiet_limit = min(dominant_db - float(quiet_delta_db), float(threshold_db) + 6.0)

        # If the overall clip is quiet, avoid classifying normal speech as background.
        if dominant_db - quiet_limit < 6.0:
            return []

        quiet_mask = active_mask & (dbfs <= quiet_limit)
        min_frames = max(1, int(round((min_duration_ms / 1000.0) / max(hop_ms / 1000.0, 1e-3))))

        intervals: list[dict] = []
        run_start = None
        for idx, flag in enumerate(quiet_mask):
            if flag and run_start is None:
                run_start = idx
            elif not flag and run_start is not None:
                if idx - run_start >= min_frames:
                    end_idx = min(len(times) - 1, idx - 1)
                    start_time = float(times[run_start])
                    end_time = float(times[end_idx] + (hop_ms / 1000.0))
                    intervals.append({
                        "start": round(start_time, 4),
                        "end": round(end_time, 4),
                        "duration": round(end_time - start_time, 4),
                        "selected": True,
                        "kind": "low_prominence_speech",
                    })
                run_start = None

        if run_start is not None and len(times) - run_start >= min_frames:
            start_time = float(times[run_start])
            end_time = float(times[-1] + (hop_ms / 1000.0))
            intervals.append({
                "start": round(start_time, 4),
                "end": round(end_time, 4),
                "duration": round(end_time - start_time, 4),
                "selected": True,
                "kind": "low_prominence_speech",
            })

        return intervals

    def get_audio_energy(self, audio_path: str, hop_length: int = 512) -> list[dict]:
        """Analyze audio energy levels over time using librosa."""
        import librosa

        y, sr = librosa.load(audio_path, sr=None)
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        times = librosa.times_like(rms, sr=sr, hop_length=hop_length)

        rms_norm = (rms - rms.min()) / (rms.max() - rms.min() + 1e-8)

        energy_data = []
        for t, e in zip(times, rms_norm):
            energy_data.append({
                "time": float(t),
                "energy": float(e)
            })

        return energy_data


# Singleton instance
audio_service = AudioService()
