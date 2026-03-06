"""
MMS_FA forced alignment service: character-level CTC alignment via torchaudio.

Replaces WhisperX's wav2vec2 alignment with torchaudio's MMS_FA pipeline,
which was purpose-built for forced alignment and achieves ~10-15ms median
word boundary accuracy (vs ~25-50ms from WhisperX).

Pipeline:
  1. Clean word texts to MMS_FA vocabulary (lowercase letters only)
  2. Tokenize as list-of-words → List[List[int]]
  3. Run MMS_FA emission model (wav2vec2 forward pass)
  4. CTC Viterbi forced alignment → List[List[TokenSpan]] (one group per word)
  5. Convert frame indices to timestamps, compute per-word confidence
"""

import logging
import re

import numpy as np
import torch
import torchaudio

from utils.audio_utils import load_audio_np

logger = logging.getLogger("finlaycut.alignment_service")

# MMS_FA model constants for frame-to-time conversion
# Total stride across 7 conv layers: 5×2×2×2×2×2×2 = 320 samples
_STRIDE = 320
# Center of the 400-sample receptive field: (400 - 1) / 2 = 199.5 samples
_RF_CENTER = 199.5

# Singleton model cache — loaded once, reused across calls
_mms_fa_bundle: torchaudio.pipelines.Wav2Vec2FABundle | None = None
_mms_fa_model: torch.nn.Module | None = None
_mms_fa_tokenizer = None
_mms_fa_aligner = None
_mms_fa_sample_rate: int | None = None


def _ensure_mms_fa_loaded():
    """Load MMS_FA model + tokenizer + aligner once, cache globally."""
    global _mms_fa_bundle, _mms_fa_model, _mms_fa_tokenizer, _mms_fa_aligner, _mms_fa_sample_rate

    if _mms_fa_model is not None:
        return

    logger.info("Loading torchaudio MMS_FA alignment model...")
    _mms_fa_bundle = torchaudio.pipelines.MMS_FA
    _mms_fa_sample_rate = _mms_fa_bundle.sample_rate  # 16000
    _mms_fa_model = _mms_fa_bundle.get_model()
    _mms_fa_model.eval()
    _mms_fa_tokenizer = _mms_fa_bundle.get_tokenizer()
    _mms_fa_aligner = _mms_fa_bundle.get_aligner()
    logger.info("MMS_FA model loaded (sample_rate=%d)", _mms_fa_sample_rate)


def _clean_word_for_tokenizer(text: str) -> str:
    """Strip a word to characters in the MMS_FA vocabulary.

    Keeps lowercase letters and apostrophe. Hyphens are excluded because
    '-' is token index 0 (the CTC blank), and the aligner rejects blank
    tokens in the target sequence.
    """
    return re.sub(r"[^a-z']", "", text.lower())


class AlignmentService:
    """Character-level forced alignment using torchaudio MMS_FA."""

    def align_segments(
        self,
        audio_path: str,
        segments: list[dict],
        padding_seconds: float = 0.5,
    ) -> list[dict]:
        """Align word timestamps for a list of transcript segments.

        Each segment dict must have:
          - "start", "end": approximate time boundaries (from faster-whisper)
          - "words": list of dicts with at least "word" key

        Returns segments with updated word timestamps and confidence scores.
        """
        _ensure_mms_fa_loaded()

        # Load full audio at MMS_FA sample rate
        try:
            waveform_np = load_audio_np(audio_path, sr=_mms_fa_sample_rate)
        except Exception as e:
            logger.error("Failed to load audio for alignment: %s", e)
            return self._fallback_passthrough(segments)

        waveform = torch.from_numpy(waveform_np).unsqueeze(0).float()  # [1, T]
        total_duration = waveform.shape[1] / _mms_fa_sample_rate

        aligned_segments = []
        for segment in segments:
            aligned_seg = self._align_single_segment(
                waveform=waveform,
                segment=segment,
                total_duration=total_duration,
                padding_seconds=padding_seconds,
            )
            aligned_segments.append(aligned_seg)

        return aligned_segments

    def _align_single_segment(
        self,
        waveform: torch.Tensor,
        segment: dict,
        total_duration: float,
        padding_seconds: float,
    ) -> dict:
        """Align a single segment's words using MMS_FA."""
        seg_start = float(segment.get("start", 0))
        seg_end = float(segment.get("end", 0))
        words = segment.get("words", [])

        if not words:
            return dict(segment)

        # Clean each word for the tokenizer (lowercase letters only)
        cleaned_words = []
        word_map = []  # Maps cleaned index → original word index
        for i, w in enumerate(words):
            cleaned = _clean_word_for_tokenizer(w.get("word", ""))
            if cleaned:
                cleaned_words.append(cleaned)
                word_map.append(i)

        if not cleaned_words:
            return self._mark_segment_fallback(segment)

        # Extract audio slice with padding
        padded_start = max(0.0, seg_start - padding_seconds)
        padded_end = min(total_duration, seg_end + padding_seconds)

        start_sample = int(padded_start * _mms_fa_sample_rate)
        end_sample = min(int(padded_end * _mms_fa_sample_rate), waveform.shape[1])

        if end_sample <= start_sample:
            return self._mark_segment_fallback(segment)

        audio_slice = waveform[:, start_sample:end_sample]
        slice_duration = (end_sample - start_sample) / _mms_fa_sample_rate

        try:
            # Tokenize: pass list of cleaned words → List[List[int]]
            word_tokens = _mms_fa_tokenizer(cleaned_words)
            if not word_tokens:
                return self._mark_segment_fallback(segment)

            # Get emission matrix from model
            with torch.no_grad():
                emission, _ = _mms_fa_model(audio_slice)
            # emission shape: [1, T_frames, C]

            num_frames = emission.shape[1]
            if num_frames == 0:
                return self._mark_segment_fallback(segment)

            # Run CTC forced alignment
            # Returns List[List[TokenSpan]] — one group of char spans per word
            word_span_groups = _mms_fa_aligner(emission[0], word_tokens)

            # Build aligned words
            aligned_words = [dict(w) for w in words]

            # Mark all words as fallback initially
            for w in aligned_words:
                w["alignment_method"] = "fallback"
                w["alignment_confidence"] = 0.0

            # Configurable offset for fine-tuning alignment timing
            from config import settings
            offset_sec = settings.alignment_timing_offset_ms / 1000.0

            # Apply alignment results for words that had valid cleaned text
            for orig_idx, span_group in zip(word_map, word_span_groups):
                if not span_group:
                    continue

                # Word boundaries from first/last character spans
                first_frame = span_group[0].start
                last_frame = span_group[-1].end

                # Stride-based conversion: each frame corresponds to _STRIDE samples,
                # offset by the receptive field center (_RF_CENTER samples)
                word_start = padded_start + (first_frame * _STRIDE + _RF_CENTER) / _mms_fa_sample_rate - offset_sec
                word_end = padded_start + (last_frame * _STRIDE + _RF_CENTER) / _mms_fa_sample_rate - offset_sec

                # Confidence: average CTC score across character spans
                avg_score = sum(s.score for s in span_group) / len(span_group)

                aligned_words[orig_idx]["start"] = round(word_start, 4)
                aligned_words[orig_idx]["end"] = round(
                    max(word_start + 0.001, word_end), 4
                )
                aligned_words[orig_idx]["alignment_confidence"] = round(
                    float(avg_score), 4
                )
                aligned_words[orig_idx]["alignment_method"] = "mms_fa"

            # Fix any overlaps
            aligned_words = self._fix_overlaps(aligned_words)

            # Build result segment
            result = dict(segment)
            result["words"] = aligned_words

            # Update segment boundaries to match aligned words
            if aligned_words:
                result["start"] = aligned_words[0].get("start", seg_start)
                result["end"] = aligned_words[-1].get("end", seg_end)

            return result

        except Exception as e:
            logger.warning(
                "MMS_FA alignment failed for segment [%.2f-%.2f]: %s",
                seg_start, seg_end, e,
            )
            return self._mark_segment_fallback(segment)

    def _fix_overlaps(self, words: list[dict]) -> list[dict]:
        """Fix overlapping word boundaries — ensure monotonic timestamps."""
        if len(words) <= 1:
            return words

        for i in range(1, len(words)):
            prev_end = words[i - 1].get("end", 0.0)
            curr_start = words[i].get("start", 0.0)
            if curr_start < prev_end:
                mid = (prev_end + curr_start) / 2.0
                words[i - 1]["end"] = round(mid, 4)
                words[i]["start"] = round(mid, 4)

        return words

    @staticmethod
    def _mark_word_fallback(word: dict) -> dict:
        """Mark a single word as using fallback alignment."""
        w = dict(word)
        w["alignment_method"] = "fallback"
        w["alignment_confidence"] = 0.0
        return w

    def _mark_segment_fallback(self, segment: dict) -> dict:
        """Mark all words in a segment as fallback."""
        result = dict(segment)
        result["words"] = [
            self._mark_word_fallback(w) for w in segment.get("words", [])
        ]
        return result

    @staticmethod
    def _fallback_passthrough(segments: list[dict]) -> list[dict]:
        """Return segments unchanged with fallback markers."""
        result = []
        for seg in segments:
            s = dict(seg)
            s["words"] = [
                {**w, "alignment_method": "fallback", "alignment_confidence": 0.0}
                for w in seg.get("words", [])
            ]
            result.append(s)
        return result


# Module-level singleton
_alignment_service: AlignmentService | None = None


def get_alignment_service() -> AlignmentService:
    """Get or create the singleton AlignmentService."""
    global _alignment_service
    if _alignment_service is None:
        _alignment_service = AlignmentService()
    return _alignment_service
