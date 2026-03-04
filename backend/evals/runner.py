"""CLI for evaluating filler, silence, and speaker quality against labeled fixtures."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from evals.metrics import evaluate_speaker_segments, evaluate_word_speakers, match_intervals
from evals.schema import EvaluationFixture
from services.audio_service import audio_service
from services.diarization_service import diarization_service


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _discover_fixture_files(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        path = Path(target).expanduser().resolve()
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"Fixture target not found: {path}")
    return files


def _load_fixture(path: Path) -> tuple[EvaluationFixture, Path]:
    payload = _load_json(path)
    fixture = EvaluationFixture.model_validate(payload)
    return fixture, path.parent


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def _ensure_transcript(
    fixture: EvaluationFixture,
    fixture_dir: Path,
    audio_path: Path,
    write_generated_transcripts: bool,
) -> dict:
    if fixture.transcript is not None:
        return copy.deepcopy(fixture.transcript)

    if fixture.transcript_path is not None:
        transcript_path = _resolve_path(fixture_dir, fixture.transcript_path)
        return _load_json(transcript_path)

    transcript = audio_service.transcribe(str(audio_path), language=fixture.language)
    if write_generated_transcripts:
        generated_path = fixture_dir / f"{fixture.id}.generated.transcript.json"
        _save_json(generated_path, transcript)
    return transcript


def _normalize_detection_segments(segments: list[dict], default_label: str | None = None) -> list[dict]:
    normalized = []
    for segment in segments:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        if end <= start:
            continue
        normalized.append(
            {
                "start": round(start, 4),
                "end": round(end, 4),
                "label": segment.get("label", default_label),
                "kind": segment.get("kind"),
            }
        )
    normalized.sort(key=lambda segment: (segment["start"], segment["end"]))
    return normalized


def _expected_interval_payload(expected: list[Any]) -> list[dict[str, Any]]:
    return [segment.model_dump() for segment in expected]


def _run_filler_evaluation(
    fixture: EvaluationFixture,
    audio_path: Path,
    transcript: dict,
) -> dict[str, Any]:
    detections = audio_service.analyze_filler_candidates(
        str(audio_path),
        transcript,
        filler_words=fixture.filler.filler_words,
    )
    predicted = _normalize_detection_segments(detections, default_label="filler")
    expected = _expected_interval_payload(fixture.filler.expected)
    metrics = match_intervals(
        predicted,
        expected,
        tolerance_seconds=float(fixture.filler.match_tolerance_ms) / 1000.0,
    )
    metrics["minimum_f1"] = fixture.filler.minimum_f1
    return {
        "predicted": predicted,
        "metrics": metrics,
    }


def _merge_detected_segments(segments: list[dict]) -> list[dict]:
    valid = []
    for segment in segments:
        start = float(segment.get("start", 0.0))
        end = float(segment.get("end", start))
        if end <= start:
            continue
        valid.append(
            {
                "start": round(start, 4),
                "end": round(end, 4),
                "kind": segment.get("kind"),
                "label": segment.get("label"),
            }
        )

    if not valid:
        return []

    valid.sort(key=lambda segment: (segment["start"], segment["end"], str(segment.get("kind"))))
    merged = [valid[0]]
    for segment in valid[1:]:
        previous = merged[-1]
        if segment["kind"] == previous.get("kind") and segment["start"] <= previous["end"]:
            previous["end"] = round(max(previous["end"], segment["end"]), 4)
        else:
            merged.append(segment)
    return merged


def _run_silence_evaluation(
    fixture: EvaluationFixture,
    audio_path: Path,
) -> dict[str, Any]:
    effective_min_duration_ms = max(
        int(fixture.silence.min_duration_ms),
        int(round((int(fixture.silence.min_duration_frames) / float(fixture.fps)) * 1000.0)),
    )
    detected_segments = audio_service.analyze_silence_candidates(
        str(audio_path),
        threshold_db=fixture.silence.threshold_db,
        min_duration_ms=effective_min_duration_ms,
    )
    predicted = _merge_detected_segments(
        _normalize_detection_segments(
            [segment for segment in detected_segments if segment.get("kind") != "low_prominence_speech"],
            default_label="silence",
        )
        + _normalize_detection_segments(
            [segment for segment in detected_segments if segment.get("kind") == "low_prominence_speech"],
            default_label="quiet_speech",
        )
    )
    expected = _expected_interval_payload(fixture.silence.expected)
    metrics = match_intervals(
        predicted,
        expected,
        tolerance_seconds=float(fixture.silence.match_tolerance_ms) / 1000.0,
    )
    metrics["minimum_f1"] = fixture.silence.minimum_f1
    metrics["effective_min_duration_ms"] = effective_min_duration_ms
    return {
        "predicted": predicted,
        "metrics": metrics,
    }


def _run_speaker_evaluation(
    fixture: EvaluationFixture,
    audio_path: Path,
    transcript: dict,
) -> dict[str, Any]:
    segments, method, fallback_reason = diarization_service.diarize_segments(
        str(audio_path),
        num_speakers=fixture.speakers.num_speakers,
    )
    result: dict[str, Any] = {
        "predicted_segments": segments,
        "method": method,
        "fallback_reason": fallback_reason,
    }

    if fixture.speakers.expected_segments:
        metrics = evaluate_speaker_segments(
            segments,
            [segment.model_dump() for segment in fixture.speakers.expected_segments],
        )
        metrics["minimum_accuracy"] = fixture.speakers.minimum_accuracy
        result["segment_metrics"] = metrics

    if fixture.speakers.expected_word_speakers:
        annotated_words = diarization_service.assign_speakers_to_words(
            copy.deepcopy(transcript.get("words", [])),
            segments,
        )
        word_metrics = evaluate_word_speakers(
            annotated_words,
            [item.model_dump() for item in fixture.speakers.expected_word_speakers],
        )
        word_metrics["minimum_accuracy"] = fixture.speakers.minimum_accuracy
        result["annotated_words"] = annotated_words
        result["word_metrics"] = word_metrics

    return result


def _metric_failed(metrics: dict[str, Any], threshold_key: str, metric_key: str) -> bool:
    threshold = metrics.get(threshold_key)
    metric_value = metrics.get(metric_key)
    if threshold is None or metric_value is None:
        return False
    return float(metric_value) < float(threshold)


def _summarize_fixture_result(result: dict[str, Any]) -> list[str]:
    lines = [f"{result['id']}"]
    if "filler" in result:
        metrics = result["filler"]["metrics"]
        lines.append(
            "  filler"
            f"  P {metrics['precision']:.2f}"
            f"  R {metrics['recall']:.2f}"
            f"  F1 {metrics['f1']:.2f}"
            f"  matched {metrics['matched_count']}/{metrics['expected_count']}"
            f"  fp {metrics['false_positive_count']}"
            f"  fn {metrics['false_negative_count']}"
        )
    if "silence" in result:
        metrics = result["silence"]["metrics"]
        lines.append(
            "  silence"
            f"  P {metrics['precision']:.2f}"
            f"  R {metrics['recall']:.2f}"
            f"  F1 {metrics['f1']:.2f}"
            f"  matched {metrics['matched_count']}/{metrics['expected_count']}"
            f"  fp {metrics['false_positive_count']}"
            f"  fn {metrics['false_negative_count']}"
        )
    if "speakers" in result:
        if "segment_metrics" in result["speakers"]:
            metrics = result["speakers"]["segment_metrics"]
            lines.append(
                "  speakers(segment)"
                f"  acc {metrics['accuracy']:.2f}"
                f"  P {metrics['precision']:.2f}"
                f"  R {metrics['recall']:.2f}"
                f"  method {result['speakers']['method']}"
            )
        if "word_metrics" in result["speakers"]:
            metrics = result["speakers"]["word_metrics"]
            lines.append(
                "  speakers(word)"
                f"  acc {metrics['accuracy']:.2f}"
                f"  P {metrics['precision']:.2f}"
                f"  R {metrics['recall']:.2f}"
                f"  method {result['speakers']['method']}"
            )
    return lines


def _collect_overall_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    overall: dict[str, list[float]] = {
        "filler_f1": [],
        "silence_f1": [],
        "speaker_accuracy": [],
    }
    for result in results:
        if "filler" in result:
            overall["filler_f1"].append(float(result["filler"]["metrics"]["f1"]))
        if "silence" in result:
            overall["silence_f1"].append(float(result["silence"]["metrics"]["f1"]))
        if "speakers" in result:
            if "segment_metrics" in result["speakers"]:
                overall["speaker_accuracy"].append(float(result["speakers"]["segment_metrics"]["accuracy"]))
            elif "word_metrics" in result["speakers"]:
                overall["speaker_accuracy"].append(float(result["speakers"]["word_metrics"]["accuracy"]))

    return {
        key: round(sum(values) / len(values), 4)
        for key, values in overall.items()
        if values
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixtures",
        nargs="+",
        default=["evals/fixtures"],
        help="Fixture files or directories containing *.json fixtures",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional path to write full JSON results",
    )
    parser.add_argument(
        "--write-generated-transcripts",
        action="store_true",
        help="Write transcribed fixtures to <fixture-id>.generated.transcript.json when transcript is missing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    fixture_files = _discover_fixture_files(args.fixtures)
    if not fixture_files:
        print("No fixture files found.", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []
    failed_threshold = False

    for fixture_file in fixture_files:
        fixture, fixture_dir = _load_fixture(fixture_file)
        if not fixture.enabled:
            continue

        audio_path = _resolve_path(fixture_dir, fixture.audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found for fixture {fixture.id}: {audio_path}")

        needs_transcript = fixture.filler.enabled or fixture.speakers.enabled
        transcript = (
            _ensure_transcript(
                fixture,
                fixture_dir,
                audio_path,
                write_generated_transcripts=args.write_generated_transcripts,
            )
            if needs_transcript
            else None
        )

        fixture_result: dict[str, Any] = {
            "id": fixture.id,
            "audio_path": str(audio_path),
            "notes": fixture.notes,
            "fixture_path": str(fixture_file),
        }
        if fixture.filler.enabled and transcript is not None:
            fixture_result["filler"] = _run_filler_evaluation(fixture, audio_path, transcript)
            failed_threshold = failed_threshold or _metric_failed(
                fixture_result["filler"]["metrics"],
                "minimum_f1",
                "f1",
            )
        if fixture.silence.enabled:
            fixture_result["silence"] = _run_silence_evaluation(fixture, audio_path)
            failed_threshold = failed_threshold or _metric_failed(
                fixture_result["silence"]["metrics"],
                "minimum_f1",
                "f1",
            )
        if fixture.speakers.enabled and transcript is not None:
            fixture_result["speakers"] = _run_speaker_evaluation(fixture, audio_path, transcript)
            if "segment_metrics" in fixture_result["speakers"]:
                failed_threshold = failed_threshold or _metric_failed(
                    fixture_result["speakers"]["segment_metrics"],
                    "minimum_accuracy",
                    "accuracy",
                )
            if "word_metrics" in fixture_result["speakers"]:
                failed_threshold = failed_threshold or _metric_failed(
                    fixture_result["speakers"]["word_metrics"],
                    "minimum_accuracy",
                    "accuracy",
                )
        results.append(fixture_result)

    if not results:
        print("No enabled fixtures found. Add labeled fixtures under evals/fixtures and set enabled=true.")
        payload = {
            "results": [],
            "overall": {},
            "fixture_count": 0,
            "failed_threshold": False,
        }
        if args.json_out:
            _save_json(Path(args.json_out).expanduser().resolve(), payload)
        return 0

    overall = _collect_overall_metrics(results)

    for result in results:
        for line in _summarize_fixture_result(result):
            print(line)
    if overall:
        print("overall")
        for key, value in overall.items():
            print(f"  {key} {value:.2f}")

    payload = {
        "results": results,
        "overall": overall,
        "fixture_count": len(results),
        "failed_threshold": failed_threshold,
    }
    if args.json_out:
        _save_json(Path(args.json_out).expanduser().resolve(), payload)

    return 1 if failed_threshold else 0


if __name__ == "__main__":
    raise SystemExit(main())
