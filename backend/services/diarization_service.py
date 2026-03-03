"""Speaker diarization service: identify who is speaking when."""

import logging
import threading
import asyncio
import time
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("finlaycut.diarization")


class DiarizationService:
    def __init__(self):
        self._pipeline = None

    def _load_pipeline(self):
        """Load the diarization pipeline (pyannote.audio)."""
        if self._pipeline is not None:
            return

        try:
            from pyannote.audio import Pipeline
            import torch

            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
            )
            if torch.backends.mps.is_available():
                self._pipeline.to(torch.device("mps"))
            elif torch.cuda.is_available():
                self._pipeline.to(torch.device("cuda"))

            logger.info("Loaded pyannote diarization pipeline")
        except ImportError:
            raise ImportError(
                "Speaker diarization requires pyannote.audio. "
                "Install with: pip install pyannote.audio"
            )
        except Exception as e:
            logger.error("Failed to load diarization pipeline: %s", e)
            raise

    def diarize(self, audio_path: str, num_speakers: Optional[int] = None) -> list[dict]:
        """
        Run speaker diarization on an audio file.

        Returns list of dicts with:
          - speaker: str (e.g., "SPEAKER_00")
          - start: float (seconds)
          - end: float (seconds)
        """
        self._load_pipeline()

        kwargs = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers

        diarization = self._pipeline(audio_path, **kwargs)

        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({
                "speaker": speaker,
                "start": round(turn.start, 3),
                "end": round(turn.end, 3),
                "duration": round(turn.end - turn.start, 3),
            })

        # Sort by start time
        segments.sort(key=lambda s: s["start"])

        # Assign color indices to speakers
        speakers = list(dict.fromkeys(s["speaker"] for s in segments))
        speaker_map = {name: i for i, name in enumerate(speakers)}
        for seg in segments:
            seg["speaker_index"] = speaker_map[seg["speaker"]]

        logger.info("Diarization complete: %d segments, %d speakers", len(segments), len(speakers))
        return segments

    async def diarize_with_progress(
        self,
        audio_path: str,
        num_speakers: Optional[int] = None,
        progress_fn: Optional[Callable[[float, str], Awaitable]] = None,
    ) -> list[dict]:
        """Run diarization in a background thread with progress updates."""
        result_holder = [None]
        error_holder = [None]
        done_event = threading.Event()

        def run_diarize():
            try:
                result_holder[0] = self.diarize(audio_path, num_speakers)
            except Exception as e:
                error_holder[0] = e
            finally:
                done_event.set()

        if progress_fn:
            await progress_fn(0.05, "Loading diarization model...")

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

        segments = result_holder[0]

        if progress_fn:
            speakers = list(dict.fromkeys(s["speaker"] for s in segments))
            await progress_fn(1.0, f"Found {len(speakers)} speakers in {len(segments)} segments")

        return segments

    def assign_speakers_to_words(
        self,
        words: list[dict],
        diarization_segments: list[dict],
    ) -> list[dict]:
        """
        Assign speaker labels to transcript words based on diarization segments.
        Uses the diarization segment with maximum overlap for each word.
        """
        for word in words:
            word_start = word["start"]
            word_end = word["end"]
            best_speaker = None
            best_overlap = 0

            for seg in diarization_segments:
                overlap_start = max(word_start, seg["start"])
                overlap_end = min(word_end, seg["end"])
                overlap = max(0, overlap_end - overlap_start)

                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = seg["speaker"]
                    word["speaker_index"] = seg["speaker_index"]

            word["speaker"] = best_speaker

        return words


diarization_service = DiarizationService()
