"""
Audio analysis service: WhisperX transcription, silence detection, filler word detection.
"""

import os
import json
import hashlib
import asyncio
import logging
import time
import threading
from pathlib import Path
from typing import Optional, Callable, Awaitable
from config import settings
from utils.audio_utils import extract_audio, get_audio_duration, load_audio_np

logger = logging.getLogger("finlaycut.audio_service")


class AudioService:
    def __init__(self):
        self._whisper_model = None
        self._align_model = None
        self._align_metadata = None
        self._model_name = settings.whisper_model
        self._device = settings.whisper_device
        self._compute_type = settings.whisper_compute_type
        self._transcript_cache_version = "v3_whisperx"

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
            "Umm, let me think like, hmm... Okay, here's what I'm, like, thinking. "
            "So, um, yeah, uh, you know what I mean? Like, basically, actually, "
            "literally, right? I mean, kind of, sort of, er, ah, well."
        )

        logger.info("Loading WhisperX model: %s (device=%s, compute=%s)", name, self._device, self._compute_type)
        asr_opts = {
            "initial_prompt": filler_prompt,
            "condition_on_previous_text": False,  # Re-apply prompt every segment
        }
        try:
            # hotwords boosts probability of filler words (requires faster-whisper >= 1.0)
            self._whisper_model = whisperx.load_model(
                name,
                device=self._device,
                compute_type=self._compute_type,
                asr_options={
                    **asr_opts,
                    "hotwords": "um uh like you know basically actually literally I mean kind of sort of er ah hmm well",
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
        self._transcript_cache_version = "v4_fillers"  # Bust old caches
        logger.info("WhisperX model loaded: %s (filler word detection enabled)", name)

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
            logger.info("Forced alignment complete")
        except Exception as e:
            logger.warning("Forced alignment failed (%s), using raw timestamps", e)

        transcript = self._convert_result(result, detected_language)

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

                # Step 2: Forced alignment
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
                    logger.info("Forced alignment complete")
                except Exception as e:
                    logger.warning("Forced alignment failed (%s), using raw timestamps", e)

                result_holder[0] = (result, detected_language)
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

        # Join with timeout to prevent infinite hang if thread is stuck
        thread.join(timeout=600)
        if thread.is_alive():
            logger.error("Transcription thread hung after 10 minutes — aborting")
            raise RuntimeError("Transcription timed out after 10 minutes. The audio file may be too large or corrupt.")

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

        result, detected_language = result_holder[0]
        transcript = self._convert_result(result, detected_language)

        # Cache the result
        if use_cache:
            cache_key = self._get_cache_key(audio_path, language)
            self._save_transcript_cache(cache_key, transcript)

        if progress_fn:
            await progress_fn(0.95, "Transcription complete")

        return transcript

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

            for word in segment.get("words", []):
                # WhisperX word dicts have "word", "start", "end", "score"
                word_data = {
                    "word": word.get("word", "").strip(),
                    "start": word.get("start", seg_data["start"]),
                    "end": word.get("end", seg_data["end"]),
                    "probability": word.get("score", 1.0),
                }
                seg_data["words"].append(word_data)
                transcript["words"].append(word_data)

            transcript["segments"].append(seg_data)

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
        import re

        custom_fillers = filler_words or settings.filler_words

        # Regex patterns that catch elongated variants Whisper commonly produces
        filler_patterns = [
            re.compile(r"^u+[hm]+$"),          # um, umm, ummm, uh, uhh, uhhh
            re.compile(r"^[hm]+$"),             # hm, hmm, hmmm, mm, mmm
            re.compile(r"^[ae]+[rh]+$"),        # er, err, ah, ahh, eh
            re.compile(r"^o+[hk]+$"),           # oh, ohh, ok (but not "on", "or")
        ]

        # Exact single-word fillers (lowercased)
        exact_fillers = {
            "um", "uh", "like", "basically", "actually", "literally",
            "right", "so", "well", "yeah", "okay", "ok",
            "just", "really", "anyway", "anyways", "whatever",
        }
        # Add any custom fillers from settings (single-word ones)
        for f in custom_fillers:
            if " " not in f:
                exact_fillers.add(f.lower())

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
            return re.sub(r"[^a-z]", "", text.lower())

        detections = []
        words = transcript.get("words", [])
        detected_indices: set[int] = set()  # Track which word indices are already detected

        # Single-word detection
        for i, word_data in enumerate(words):
            cleaned = _clean(word_data.get("word", ""))
            if not cleaned:
                continue

            is_filler = cleaned in exact_fillers or any(p.match(cleaned) for p in filler_patterns)
            if is_filler:
                detected_indices.add(i)
                context_start = max(0, i - 3)
                context_end = min(len(words), i + 4)
                context = " ".join(w["word"] for w in words[context_start:context_end])
                detections.append({
                    "word": word_data["word"],
                    "start": word_data["start"],
                    "end": word_data["end"],
                    "context": context,
                    "selected": True,
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
                        "context": context,
                        "selected": True,
                    })

        detections.sort(key=lambda d: d["start"])
        return detections

    def get_audio_energy(self, audio_path: str, hop_length: int = 512) -> list[dict]:
        """Analyze audio energy levels over time using librosa."""
        import librosa
        import numpy as np

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
