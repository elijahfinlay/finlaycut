"""
Audio analysis service: Whisper transcription, silence detection, filler word detection.
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Optional
from config import settings
from utils.audio_utils import extract_audio, get_audio_duration


class AudioService:
    def __init__(self):
        self._whisper_model = None
        self._model_name = settings.whisper_model

    def _get_cache_key(self, audio_path: str) -> str:
        """Generate a cache key based on file path and modification time."""
        stat = os.stat(audio_path)
        raw = f"{audio_path}:{stat.st_size}:{stat.st_mtime}"
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

    def load_whisper_model(self, model_name: str | None = None):
        """Load or reload the Whisper model."""
        name = model_name or self._model_name
        if self._whisper_model is not None and self._model_name == name:
            return

        import stable_whisper
        print(f"[AudioService] Loading Whisper model: {name}")
        self._whisper_model = stable_whisper.load_model(name)
        self._model_name = name
        print(f"[AudioService] Whisper model loaded: {name}")

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        use_cache: bool = True,
        progress_callback=None
    ) -> dict:
        """
        Transcribe audio using Whisper with word-level timestamps.

        Returns dict with:
            - text: full transcript text
            - segments: list of segments with start, end, text
            - words: list of words with start, end, word, probability
        """
        # Check cache
        if use_cache:
            cache_key = self._get_cache_key(audio_path)
            cached = self._get_cached_transcript(cache_key)
            if cached is not None:
                print(f"[AudioService] Using cached transcript for {audio_path}")
                return cached

        self.load_whisper_model()

        kwargs = {"word_timestamps": True}
        if language:
            kwargs["language"] = language

        if progress_callback:
            progress_callback(0.1, "Starting transcription...")

        result = self._whisper_model.transcribe(audio_path, **kwargs)

        if progress_callback:
            progress_callback(0.8, "Processing results...")

        # Convert to our format
        transcript = {
            "text": result.text,
            "language": result.language if hasattr(result, 'language') else None,
            "segments": [],
            "words": []
        }

        for segment in result.segments:
            seg_data = {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "words": []
            }

            if hasattr(segment, 'words') and segment.words:
                for word in segment.words:
                    word_data = {
                        "word": word.word.strip(),
                        "start": word.start,
                        "end": word.end,
                        "probability": getattr(word, 'probability', 1.0)
                    }
                    seg_data["words"].append(word_data)
                    transcript["words"].append(word_data)

            transcript["segments"].append(seg_data)

        # Cache the result
        if use_cache:
            self._save_transcript_cache(cache_key, transcript)

        if progress_callback:
            progress_callback(1.0, "Transcription complete")

        return transcript

    def detect_silence(
        self,
        audio_path: str,
        threshold_db: int = -40,
        min_duration_ms: int = 300,
        progress_callback=None
    ) -> list[dict]:
        """
        Detect silent segments in audio.

        Returns list of dicts with:
            - start: start time in seconds
            - end: end time in seconds
            - duration: duration in seconds
        """
        from pydub import AudioSegment
        from pydub.silence import detect_silence as pydub_detect_silence

        if progress_callback:
            progress_callback(0.1, "Loading audio...")

        audio = AudioSegment.from_file(audio_path)
        total_duration = len(audio) / 1000.0

        if progress_callback:
            progress_callback(0.3, "Detecting silence...")

        # pydub returns list of [start_ms, end_ms] for silent segments
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
                "selected": True  # User can deselect in the UI
            })

        if progress_callback:
            progress_callback(1.0, f"Found {len(silences)} silent segments")

        return silences

    def detect_filler_words(
        self,
        transcript: dict,
        filler_words: list[str] | None = None
    ) -> list[dict]:
        """
        Detect filler words in a transcript.

        Returns list of dicts with:
            - word: the filler word
            - start: start time in seconds
            - end: end time in seconds
            - context: surrounding words for context
            - selected: True (user can deselect)
        """
        fillers_list = filler_words or settings.filler_words
        # Normalize to lowercase
        fillers_set = {f.lower() for f in fillers_list}

        detections = []
        words = transcript.get("words", [])

        for i, word_data in enumerate(words):
            word_text = word_data["word"].lower().strip(" .,!?")

            if word_text in fillers_set:
                # Build context (3 words before and after)
                context_start = max(0, i - 3)
                context_end = min(len(words), i + 4)
                context_words = [w["word"] for w in words[context_start:context_end]]
                context = " ".join(context_words)

                detections.append({
                    "word": word_data["word"],
                    "start": word_data["start"],
                    "end": word_data["end"],
                    "context": context,
                    "selected": True
                })

        # Also check for multi-word fillers like "you know", "I mean"
        for filler in fillers_list:
            if " " in filler:
                filler_words_list = filler.lower().split()
                filler_len = len(filler_words_list)
                for i in range(len(words) - filler_len + 1):
                    window = [words[i + j]["word"].lower().strip(" .,!?") for j in range(filler_len)]
                    if window == filler_words_list:
                        context_start = max(0, i - 3)
                        context_end = min(len(words), i + filler_len + 3)
                        context_words = [w["word"] for w in words[context_start:context_end]]

                        detections.append({
                            "word": filler,
                            "start": words[i]["start"],
                            "end": words[i + filler_len - 1]["end"],
                            "context": " ".join(context_words),
                            "selected": True
                        })

        # Sort by start time and deduplicate overlapping detections
        detections.sort(key=lambda d: d["start"])
        deduped = []
        for d in detections:
            if not deduped or d["start"] >= deduped[-1]["end"]:
                deduped.append(d)

        return deduped

    def get_audio_energy(self, audio_path: str, hop_length: int = 512) -> list[dict]:
        """
        Analyze audio energy levels over time using librosa.
        Used for emphasis detection in zoom cuts.
        """
        import librosa
        import numpy as np

        y, sr = librosa.load(audio_path, sr=None)
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        times = librosa.times_like(rms, sr=sr, hop_length=hop_length)

        # Normalize to 0-1
        rms_norm = (rms - rms.min()) / (rms.max() - rms.min() + 1e-8)

        # Find peaks (emphasis points)
        energy_data = []
        for t, e in zip(times, rms_norm):
            energy_data.append({
                "time": float(t),
                "energy": float(e)
            })

        return energy_data


# Singleton instance
audio_service = AudioService()
