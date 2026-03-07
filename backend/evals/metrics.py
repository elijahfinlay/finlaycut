"""Metrics for offline filler, silence, speaker, and alignment evaluations."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import numpy as np

from evals.schema import AlignmentEvaluationConfig, WordAlignmentExpectation

logger = logging.getLogger("finlaycut.evals.metrics")


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _normalize_interval(segment: dict[str, Any]) -> dict[str, Any] | None:
    try:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
    except Exception:
        return None
    if end <= start:
        return None
    normalized = {
        "start": round(start, 4),
        "end": round(end, 4),
    }
    for key in ("label", "kind", "speaker", "word"):
        if segment.get(key) is not None:
            normalized[key] = segment.get(key)
    return normalized


def _interval_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    return max(0.0, min(float(a["end"]), float(b["end"])) - max(float(a["start"]), float(b["start"])))


def _intervals_match(a: dict[str, Any], b: dict[str, Any], tolerance_seconds: float) -> bool:
    return float(a["end"]) >= float(b["start"]) - tolerance_seconds and float(a["start"]) <= float(b["end"]) + tolerance_seconds


def _labels_compatible(predicted: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key in ("label", "kind", "speaker"):
        expected_value = expected.get(key)
        predicted_value = predicted.get(key)
        if expected_value is None or predicted_value is None:
            continue
        if str(expected_value) != str(predicted_value):
            return False
    return True


def match_intervals(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    tolerance_seconds: float = 0.0,
) -> dict[str, Any]:
    normalized_predicted = [segment for item in predicted if (segment := _normalize_interval(item)) is not None]
    normalized_expected = [segment for item in expected if (segment := _normalize_interval(item)) is not None]

    used_predicted: set[int] = set()
    matches: list[dict[str, Any]] = []

    for expected_index, expected_segment in enumerate(normalized_expected):
        candidates: list[tuple[float, float, int, dict[str, Any]]] = []
        for predicted_index, predicted_segment in enumerate(normalized_predicted):
            if predicted_index in used_predicted:
                continue
            if not _labels_compatible(predicted_segment, expected_segment):
                continue
            if not _intervals_match(predicted_segment, expected_segment, tolerance_seconds):
                continue
            overlap = _interval_overlap(predicted_segment, expected_segment)
            boundary_error = (
                abs(float(predicted_segment["start"]) - float(expected_segment["start"]))
                + abs(float(predicted_segment["end"]) - float(expected_segment["end"]))
            )
            candidates.append((overlap, -boundary_error, predicted_index, predicted_segment))

        if not candidates:
            continue

        candidates.sort(reverse=True)
        overlap, _neg_boundary_error, predicted_index, predicted_segment = candidates[0]
        used_predicted.add(predicted_index)
        matches.append({
            "expected_index": expected_index,
            "predicted_index": predicted_index,
            "overlap_seconds": round(overlap, 4),
            "expected": expected_segment,
            "predicted": predicted_segment,
        })

    matched_count = len(matches)
    predicted_count = len(normalized_predicted)
    expected_count = len(normalized_expected)
    false_positive_count = max(0, predicted_count - matched_count)
    false_negative_count = max(0, expected_count - matched_count)
    precision = _safe_div(matched_count, predicted_count)
    recall = _safe_div(matched_count, expected_count)
    f1 = _safe_div(2.0 * precision * recall, precision + recall)

    return {
        "predicted_count": predicted_count,
        "expected_count": expected_count,
        "matched_count": matched_count,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "matches": matches,
    }


def _active_speaker(segments: list[dict[str, Any]], midpoint: float) -> str | None:
    for segment in segments:
        if float(segment["start"]) <= midpoint < float(segment["end"]):
            speaker = segment.get("speaker")
            return str(speaker) if speaker is not None else None
    return None


def evaluate_speaker_segments(
    predicted_segments: list[dict[str, Any]],
    expected_segments: list[dict[str, Any]],
    tolerance_seconds: float = 0.15,
) -> dict[str, Any]:
    normalized_predicted = [
        segment for item in predicted_segments if (segment := _normalize_interval(item)) is not None
    ]
    normalized_expected = [
        segment for item in expected_segments if (segment := _normalize_interval(item)) is not None
    ]
    interval_metrics = match_intervals(
        normalized_predicted,
        normalized_expected,
        tolerance_seconds=tolerance_seconds,
    )

    boundaries = sorted({
        round(float(segment["start"]), 4)
        for segment in [*normalized_predicted, *normalized_expected]
    } | {
        round(float(segment["end"]), 4)
        for segment in [*normalized_predicted, *normalized_expected]
    })

    total_duration = 0.0
    correct_duration = 0.0
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= left:
            continue
        midpoint = (left + right) / 2.0
        expected_speaker = _active_speaker(normalized_expected, midpoint)
        if expected_speaker is None:
            continue
        predicted_speaker = _active_speaker(normalized_predicted, midpoint)
        duration = right - left
        total_duration += duration
        if predicted_speaker == expected_speaker:
            correct_duration += duration

    accuracy = _safe_div(correct_duration, total_duration)
    return {
        **interval_metrics,
        "accuracy": round(accuracy, 4),
        "matched_duration_seconds": round(correct_duration, 4),
        "expected_duration_seconds": round(total_duration, 4),
    }


def _normalize_word(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def evaluate_word_speakers(
    annotated_words: list[dict[str, Any]],
    expected_word_speakers: list[dict[str, Any]],
    tolerance_seconds: float = 0.25,
) -> dict[str, Any]:
    predicted_words = []
    for word in annotated_words:
        if word.get("speaker") is None:
            continue
        try:
            predicted_words.append({
                "word": str(word.get("word", "")),
                "normalized_word": _normalize_word(str(word.get("word", ""))),
                "start": float(word.get("start", 0.0)),
                "end": float(word.get("end", 0.0)),
                "speaker": str(word.get("speaker")),
            })
        except Exception:
            continue

    expected_words = []
    for item in expected_word_speakers:
        try:
            expected_words.append({
                "word": str(item.get("word", "")),
                "normalized_word": _normalize_word(str(item.get("word", ""))),
                "start": float(item.get("start", 0.0)),
                "end": float(item.get("end", 0.0)),
                "speaker": str(item.get("speaker")),
            })
        except Exception:
            continue

    used_predicted: set[int] = set()
    matched_count = 0
    correct_count = 0

    for expected_word in expected_words:
        expected_mid = (expected_word["start"] + expected_word["end"]) / 2.0
        candidates: list[tuple[float, int, dict[str, Any]]] = []
        for predicted_index, predicted_word in enumerate(predicted_words):
            if predicted_index in used_predicted:
                continue
            if expected_word["normalized_word"] and predicted_word["normalized_word"] != expected_word["normalized_word"]:
                continue
            predicted_mid = (predicted_word["start"] + predicted_word["end"]) / 2.0
            distance = abs(predicted_mid - expected_mid)
            if distance > tolerance_seconds:
                continue
            candidates.append((distance, predicted_index, predicted_word))

        if not candidates:
            continue

        candidates.sort()
        _distance, predicted_index, predicted_word = candidates[0]
        used_predicted.add(predicted_index)
        matched_count += 1
        if predicted_word["speaker"] == expected_word["speaker"]:
            correct_count += 1

    predicted_count = len(predicted_words)
    expected_count = len(expected_words)
    false_positive_count = max(0, predicted_count - correct_count)
    false_negative_count = max(0, expected_count - correct_count)
    precision = _safe_div(correct_count, predicted_count)
    recall = _safe_div(correct_count, expected_count)
    f1 = _safe_div(2.0 * precision * recall, precision + recall)
    accuracy = _safe_div(correct_count, matched_count)

    return {
        "predicted_count": predicted_count,
        "expected_count": expected_count,
        "matched_count": matched_count,
        "correct_count": correct_count,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "accuracy": round(accuracy, 4),
    }


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

    def to_dict(self) -> dict[str, Any]:
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
        self.matched_words = sum(1 for result in word_results if result.matched)

        errors = [result.boundary_error_ms for result in word_results if result.matched]
        if errors:
            self.median_error_ms = float(np.median(errors))
            self.mean_error_ms = float(np.mean(errors))
            self.p95_error_ms = float(np.percentile(errors, 95))
            self.pct_within_1_frame = 100.0 * sum(1 for error in errors if error <= 33.0) / len(errors)
            self.pct_within_2_frames = 100.0 * sum(1 for error in errors if error <= 66.0) / len(errors)
            self.pct_within_tolerance = 100.0 * sum(1 for result in word_results if result.within_tolerance) / len(errors)
        else:
            self.median_error_ms = float("inf")
            self.mean_error_ms = float("inf")
            self.p95_error_ms = float("inf")
            self.pct_within_1_frame = 0.0
            self.pct_within_2_frames = 0.0
            self.pct_within_tolerance = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_words": self.total_words,
            "matched_words": self.matched_words,
            "median_error_ms": round(self.median_error_ms, 2),
            "mean_error_ms": round(self.mean_error_ms, 2),
            "p95_error_ms": round(self.p95_error_ms, 2),
            "pct_within_1_frame_33ms": round(self.pct_within_1_frame, 1),
            "pct_within_2_frames_66ms": round(self.pct_within_2_frames, 1),
            "pct_within_tolerance": round(self.pct_within_tolerance, 1),
            "per_word": [result.to_dict() for result in self.word_results],
        }

    def passes_targets(self, config: AlignmentEvaluationConfig) -> bool:
        return (
            self.median_error_ms <= config.target_median_error_ms
            and self.pct_within_1_frame >= config.target_pct_within_1_frame
            and self.pct_within_2_frames >= config.target_pct_within_2_frames
        )


def _find_matching_word(
    expected: WordAlignmentExpectation,
    transcript_words: list[dict[str, Any]],
    time_window_s: float = 2.0,
) -> Optional[dict[str, Any]]:
    target_text = _normalize_word(expected.word)
    if not target_text:
        return None

    expected_mid = (expected.expected_start + expected.expected_end) / 2.0
    candidates = []

    for word in transcript_words:
        word_text = _normalize_word(str(word.get("word", "")))
        if word_text != target_text:
            continue
        word_mid = (float(word.get("start", 0.0)) + float(word.get("end", 0.0))) / 2.0
        if abs(word_mid - expected_mid) <= time_window_s:
            candidates.append((abs(word_mid - expected_mid), word))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def evaluate_word_alignment(
    config: AlignmentEvaluationConfig,
    transcript_words: list[dict[str, Any]],
) -> AlignmentEvaluationResult:
    results = []

    for expected in config.word_expectations:
        tolerance = config.global_tolerance_ms or expected.tolerance_ms
        matched_word = _find_matching_word(expected, transcript_words)

        if matched_word is not None:
            result = WordAlignmentResult(
                word=expected.word,
                expected_start=expected.expected_start,
                expected_end=expected.expected_end,
                actual_start=float(matched_word.get("start", 0.0)),
                actual_end=float(matched_word.get("end", 0.0)),
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
            logger.warning(
                "No matching word found for '%s' at %.2f-%.2f",
                expected.word,
                expected.expected_start,
                expected.expected_end,
            )

        results.append(result)

    return AlignmentEvaluationResult(results)
