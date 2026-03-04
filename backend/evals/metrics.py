"""Metric helpers for fixture-driven evaluation."""

from __future__ import annotations

from collections import defaultdict
from itertools import permutations
from typing import Any


def _range_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(float(end_a), float(end_b)) - max(float(start_a), float(start_b)))


def _duration(segment: dict[str, Any]) -> float:
    return max(0.0, float(segment["end"]) - float(segment["start"]))


def _build_interval_match_candidates(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    tolerance_seconds: float,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for predicted_index, predicted_segment in enumerate(predicted):
        pred_label = predicted_segment.get("label")
        pred_kind = predicted_segment.get("kind")
        pred_start = float(predicted_segment["start"])
        pred_end = float(predicted_segment["end"])
        pred_duration = max(1e-6, pred_end - pred_start)

        for expected_index, expected_segment in enumerate(expected):
            expected_label = expected_segment.get("label")
            expected_kind = expected_segment.get("kind")
            if expected_label is not None and pred_label != expected_label:
                continue
            if expected_kind is not None and pred_kind != expected_kind:
                continue

            exp_start = float(expected_segment["start"])
            exp_end = float(expected_segment["end"])
            exp_duration = max(1e-6, exp_end - exp_start)
            overlap = _range_overlap(pred_start, pred_end, exp_start, exp_end)
            start_error = abs(pred_start - exp_start)
            end_error = abs(pred_end - exp_end)
            center_error = abs(((pred_start + pred_end) / 2.0) - ((exp_start + exp_end) / 2.0))
            overlap_ratio = overlap / max(pred_duration, exp_duration)
            shortest_overlap_ratio = overlap / min(pred_duration, exp_duration)

            if not (
                overlap > 0.0
                or start_error <= tolerance_seconds
                or end_error <= tolerance_seconds
                or center_error <= tolerance_seconds
                or shortest_overlap_ratio >= 0.35
            ):
                continue

            score = (
                overlap * 4.0
                + overlap_ratio * 3.0
                + shortest_overlap_ratio * 1.5
                - start_error
                - end_error
                - (center_error * 0.5)
            )
            candidates.append(
                {
                    "predicted_index": predicted_index,
                    "expected_index": expected_index,
                    "score": score,
                    "overlap_seconds": round(overlap, 4),
                    "start_error_ms": round(start_error * 1000.0, 1),
                    "end_error_ms": round(end_error * 1000.0, 1),
                    "center_error_ms": round(center_error * 1000.0, 1),
                }
            )
    return candidates


def match_intervals(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    tolerance_seconds: float,
) -> dict[str, Any]:
    candidates = _build_interval_match_candidates(predicted, expected, tolerance_seconds)
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)

    used_predicted: set[int] = set()
    used_expected: set[int] = set()
    matches: list[dict[str, Any]] = []

    for candidate in candidates:
        predicted_index = candidate["predicted_index"]
        expected_index = candidate["expected_index"]
        if predicted_index in used_predicted or expected_index in used_expected:
            continue
        used_predicted.add(predicted_index)
        used_expected.add(expected_index)
        matches.append(
            {
                **candidate,
                "predicted": predicted[predicted_index],
                "expected": expected[expected_index],
            }
        )

    false_positives = [
        predicted[index]
        for index in range(len(predicted))
        if index not in used_predicted
    ]
    false_negatives = [
        expected[index]
        for index in range(len(expected))
        if index not in used_expected and not bool(expected[index].get("optional"))
    ]
    ignored_optionals = [
        expected[index]
        for index in range(len(expected))
        if index not in used_expected and bool(expected[index].get("optional"))
    ]

    matched_required = sum(1 for match in matches if not bool(match["expected"].get("optional")))
    precision = len(matches) / len(predicted) if predicted else (1.0 if not expected else 0.0)
    recall_denominator = sum(1 for segment in expected if not bool(segment.get("optional")))
    recall = matched_required / recall_denominator if recall_denominator else 1.0
    f1 = (2.0 * precision * recall / (precision + recall)) if precision + recall else 0.0

    average_start_error_ms = (
        round(sum(match["start_error_ms"] for match in matches) / len(matches), 1) if matches else None
    )
    average_end_error_ms = (
        round(sum(match["end_error_ms"] for match in matches) / len(matches), 1) if matches else None
    )

    return {
        "predicted_count": len(predicted),
        "expected_count": len(expected),
        "matched_count": len(matches),
        "false_positive_count": len(false_positives),
        "false_negative_count": len(false_negatives),
        "ignored_optional_count": len(ignored_optionals),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "average_start_error_ms": average_start_error_ms,
        "average_end_error_ms": average_end_error_ms,
        "matches": matches,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "ignored_optionals": ignored_optionals,
    }


def _best_one_to_one_mapping(weights: dict[str, dict[str, float]]) -> tuple[dict[str, str | None], float]:
    predicted_labels = sorted(weights.keys())
    expected_labels = sorted({label for row in weights.values() for label in row.keys()})
    if not predicted_labels or not expected_labels:
        return {label: None for label in predicted_labels}, 0.0

    if len(predicted_labels) > 8 or len(expected_labels) > 8:
        mapping: dict[str, str | None] = {}
        used: set[str] = set()
        score = 0.0
        for predicted_label in predicted_labels:
            candidates = sorted(
                weights[predicted_label].items(),
                key=lambda item: item[1],
                reverse=True,
            )
            best_label = None
            best_weight = 0.0
            for label, weight in candidates:
                if label in used:
                    continue
                best_label = label
                best_weight = float(weight)
                break
            mapping[predicted_label] = best_label
            if best_label is not None:
                used.add(best_label)
                score += best_weight
        return mapping, score

    best_mapping: dict[str, str | None] = {label: None for label in predicted_labels}
    best_score = -1.0

    for assignment_size in range(min(len(predicted_labels), len(expected_labels)), -1, -1):
        for predicted_subset in permutations(predicted_labels, assignment_size):
            for expected_subset in permutations(expected_labels, assignment_size):
                mapping = {label: None for label in predicted_labels}
                score = 0.0
                for predicted_label, expected_label in zip(predicted_subset, expected_subset):
                    mapping[predicted_label] = expected_label
                    score += float(weights.get(predicted_label, {}).get(expected_label, 0.0))
                if score > best_score:
                    best_score = score
                    best_mapping = mapping
        if best_score >= 0:
            break

    return best_mapping, max(0.0, best_score)


def evaluate_speaker_segments(
    predicted: list[dict[str, Any]],
    expected: list[dict[str, Any]],
) -> dict[str, Any]:
    overlap_weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    expected_duration = 0.0
    predicted_duration = 0.0

    for expected_segment in expected:
        if bool(expected_segment.get("optional")):
            continue
        expected_duration += _duration(expected_segment)

    for predicted_segment in predicted:
        predicted_duration += _duration(predicted_segment)
        predicted_speaker = str(predicted_segment.get("speaker"))
        for expected_segment in expected:
            if bool(expected_segment.get("optional")):
                continue
            overlap = _range_overlap(
                predicted_segment["start"],
                predicted_segment["end"],
                expected_segment["start"],
                expected_segment["end"],
            )
            if overlap <= 0.0:
                continue
            overlap_weights[predicted_speaker][str(expected_segment["speaker"])] += overlap

    mapping, matched_duration = _best_one_to_one_mapping(overlap_weights)
    precision = matched_duration / predicted_duration if predicted_duration else 0.0
    recall = matched_duration / expected_duration if expected_duration else 1.0
    f1 = (2.0 * precision * recall / (precision + recall)) if precision + recall else 0.0

    return {
        "mapping": mapping,
        "matched_duration_seconds": round(matched_duration, 4),
        "expected_duration_seconds": round(expected_duration, 4),
        "predicted_duration_seconds": round(predicted_duration, 4),
        "accuracy": round(recall, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "overlap_weights": {
            predicted_label: {
                expected_label: round(weight, 4)
                for expected_label, weight in row.items()
            }
            for predicted_label, row in overlap_weights.items()
        },
    }


def evaluate_word_speakers(
    predicted_words: list[dict[str, Any]],
    expectations: list[dict[str, Any]],
) -> dict[str, Any]:
    indexed_words = {
        index: predicted_words[index]
        for index in range(len(predicted_words))
    }
    weights: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    labeled_count = 0
    prediction_count = 0

    for expectation in expectations:
        if bool(expectation.get("optional")):
            continue
        word_index = int(expectation["word_index"])
        predicted_word = indexed_words.get(word_index)
        if predicted_word is None:
            continue
        labeled_count += 1
        predicted_speaker = predicted_word.get("speaker")
        if predicted_speaker is None:
            continue
        prediction_count += 1
        weights[str(predicted_speaker)][str(expectation["speaker"])] += 1.0

    mapping, matched = _best_one_to_one_mapping(weights)
    accuracy = matched / labeled_count if labeled_count else 1.0
    precision = matched / prediction_count if prediction_count else 0.0
    recall = accuracy
    f1 = (2.0 * precision * recall / (precision + recall)) if precision + recall else 0.0

    return {
        "mapping": mapping,
        "matched_word_count": int(round(matched)),
        "labeled_word_count": labeled_count,
        "predicted_word_count": prediction_count,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
