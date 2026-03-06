"""
Alignment accuracy evaluation metrics.

Compares transcription output against ground-truth word boundaries
and reports per-word error, median error, and percentage within tolerance.
"""

import logging
from typing import Optional

import numpy as np

from evals.schema import AlignmentEvaluationConfig, WordAlignmentExpectation

logger = logging.getLogger("finlaycut.evals.metrics")


class WordAlignmentResult:
    """Result for a single word comparison."""
    def __init__(
        self,
        word: str,
        expected_start: float,
        expected_end: float,
        actual_start: Optional[float],
        actual_end: Optional[float],
        tolerance_ms: float,
    ):
        self.word = word
        self.expected_start = expected_start
        self.expected_end = expected_end
        self.actual_start = actual_start
        self.actual_end = actual_end
        self.tolerance_ms = tolerance_ms

        if actual_start is not None and actual_end is not None:
            self.start_error_ms = abs(actual_start - expected_start) * 1000.0
            self.end_error_ms = abs(actual_end - expected_end) * 1000.0
            self.boundary_error_ms = (self.start_error_ms + self.end_error_ms) / 2.0
            self.matched = True
        else:
            self.start_error_ms = float("inf")
            self.end_error_ms = float("inf")
            self.boundary_error_ms = float("inf")
            self.matched = False

        self.within_tolerance = self.boundary_error_ms <= tolerance_ms

    def to_dict(self) -> dict:
        return {
            "word": self.word,
            "expected_start": self.expected_start,
            "expected_end": self.expected_end,
            "actual_start": self.actual_start,
            "actual_end": self.actual_end,
            "start_error_ms": round(self.start_error_ms, 2),
            "end_error_ms": round(self.end_error_ms, 2),
            "boundary_error_ms": round(self.boundary_error_ms, 2),
            "within_tolerance": self.within_tolerance,
            "matched": self.matched,
        }


class AlignmentEvaluationResult:
    """Aggregate result for an alignment evaluation."""
    def __init__(self, word_results: list[WordAlignmentResult]):
        self.word_results = word_results
        self.total_words = len(word_results)
        self.matched_words = sum(1 for r in word_results if r.matched)

        errors = [r.boundary_error_ms for r in word_results if r.matched]
        if errors:
            self.median_error_ms = float(np.median(errors))
            self.mean_error_ms = float(np.mean(errors))
            self.p95_error_ms = float(np.percentile(errors, 95))
            self.pct_within_1_frame = 100.0 * sum(1 for e in errors if e <= 33.0) / len(errors)
            self.pct_within_2_frames = 100.0 * sum(1 for e in errors if e <= 66.0) / len(errors)
            self.pct_within_tolerance = 100.0 * sum(1 for r in word_results if r.within_tolerance) / len(errors)
        else:
            self.median_error_ms = float("inf")
            self.mean_error_ms = float("inf")
            self.p95_error_ms = float("inf")
            self.pct_within_1_frame = 0.0
            self.pct_within_2_frames = 0.0
            self.pct_within_tolerance = 0.0

    def to_dict(self) -> dict:
        return {
            "total_words": self.total_words,
            "matched_words": self.matched_words,
            "median_error_ms": round(self.median_error_ms, 2),
            "mean_error_ms": round(self.mean_error_ms, 2),
            "p95_error_ms": round(self.p95_error_ms, 2),
            "pct_within_1_frame_33ms": round(self.pct_within_1_frame, 1),
            "pct_within_2_frames_66ms": round(self.pct_within_2_frames, 1),
            "pct_within_tolerance": round(self.pct_within_tolerance, 1),
            "per_word": [r.to_dict() for r in self.word_results],
        }

    def passes_targets(self, config: AlignmentEvaluationConfig) -> bool:
        """Check if results meet the configured accuracy targets."""
        return (
            self.median_error_ms <= config.target_median_error_ms
            and self.pct_within_1_frame >= config.target_pct_within_1_frame
            and self.pct_within_2_frames >= config.target_pct_within_2_frames
        )


def _normalize_word(text: str) -> str:
    """Normalize word for fuzzy matching."""
    import re
    return re.sub(r"[^a-z]", "", text.lower())


def _find_matching_word(
    expected: WordAlignmentExpectation,
    transcript_words: list[dict],
    time_window_s: float = 2.0,
) -> Optional[dict]:
    """Find the best matching transcript word for an expected word.

    Matches by normalized text within a time window around the expected position.
    """
    target_text = _normalize_word(expected.word)
    if not target_text:
        return None

    expected_mid = (expected.expected_start + expected.expected_end) / 2.0
    candidates = []

    for w in transcript_words:
        w_text = _normalize_word(w.get("word", ""))
        if w_text != target_text:
            continue
        w_mid = (float(w.get("start", 0)) + float(w.get("end", 0))) / 2.0
        if abs(w_mid - expected_mid) <= time_window_s:
            candidates.append((abs(w_mid - expected_mid), w))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def evaluate_word_alignment(
    config: AlignmentEvaluationConfig,
    transcript_words: list[dict],
) -> AlignmentEvaluationResult:
    """Evaluate word alignment accuracy against ground-truth expectations.

    Args:
        config: Evaluation config with expected word boundaries.
        transcript_words: Actual transcript words from the alignment pipeline.

    Returns:
        AlignmentEvaluationResult with per-word and aggregate metrics.
    """
    results = []

    for expected in config.word_expectations:
        tolerance = config.global_tolerance_ms or expected.tolerance_ms
        matched_word = _find_matching_word(expected, transcript_words)

        if matched_word is not None:
            result = WordAlignmentResult(
                word=expected.word,
                expected_start=expected.expected_start,
                expected_end=expected.expected_end,
                actual_start=float(matched_word.get("start", 0)),
                actual_end=float(matched_word.get("end", 0)),
                tolerance_ms=tolerance,
            )
        else:
            result = WordAlignmentResult(
                word=expected.word,
                expected_start=expected.expected_start,
                expected_end=expected.expected_end,
                actual_start=None,
                actual_end=None,
                tolerance_ms=tolerance,
            )
            logger.warning("No matching word found for '%s' at %.2f-%.2f",
                         expected.word, expected.expected_start, expected.expected_end)

        results.append(result)

    return AlignmentEvaluationResult(results)
