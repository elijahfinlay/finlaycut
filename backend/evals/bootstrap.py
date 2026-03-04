"""Bootstrap a starter evaluation fixture from the current Resolve timeline."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

from config import settings
from services.audio_service import audio_service
from services.diarization_service import diarization_service
from services.resolve_service import resolve_service
from utils.audio_utils import assemble_timeline_audio, extract_audio, extract_audio_segment


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return slug.strip("_") or "fixture"


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def _effective_min_silence_ms(fps: float) -> int:
    return max(
        int(settings.silence_min_duration_ms),
        int(round((max(14, int(settings.silence_min_duration_frames)) / fps) * 1000.0)),
    )


def _detect_silence_predictions(audio_path: str, fps: float) -> list[dict[str, Any]]:
    effective_min_duration_ms = _effective_min_silence_ms(fps)
    detected_segments = audio_service.analyze_silence_candidates(
        audio_path,
        threshold_db=settings.silence_threshold_db,
        min_duration_ms=effective_min_duration_ms,
    )
    combined = [
        {
            "start": round(float(segment["start"]), 4),
            "end": round(float(segment["end"]), 4),
            "kind": segment.get("kind") or "silence",
        }
        for segment in detected_segments
        if float(segment["end"]) > float(segment["start"])
    ]
    combined.sort(key=lambda segment: (segment["start"], segment["end"], str(segment.get("kind"))))
    return combined


def _build_fixture_payload(
    fixture_id: str,
    audio_rel_path: str,
    transcript_rel_path: str,
    predictions_rel_path: str,
    fps: float,
    language: str | None,
    notes: str,
    filler_predictions: list[dict[str, Any]],
    silence_predictions: list[dict[str, Any]],
    speaker_predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": fixture_id,
        "enabled": False,
        "audio_path": audio_rel_path,
        "fps": fps,
        "language": language,
        "transcript_path": transcript_rel_path,
        "notes": f"{notes} Review placeholder expectations from {predictions_rel_path} before enabling.",
        "filler": {
            "enabled": True,
            "match_tolerance_ms": 140,
            "minimum_f1": 0.9,
            "expected": [
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "label": "filler",
                }
                for segment in filler_predictions
            ],
        },
        "silence": {
            "enabled": True,
            "threshold_db": settings.silence_threshold_db,
            "min_duration_ms": settings.silence_min_duration_ms,
            "min_duration_frames": max(14, int(settings.silence_min_duration_frames)),
            "match_tolerance_ms": 160,
            "minimum_f1": 0.85,
            "expected": [
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "kind": segment.get("kind"),
                }
                for segment in silence_predictions
            ],
        },
        "speakers": {
            "enabled": True,
            "num_speakers": None,
            "minimum_accuracy": 0.9,
            "expected_segments": [
                {
                    "start": round(float(segment["start"]), 4),
                    "end": round(float(segment["end"]), 4),
                    "speaker": str(segment["speaker"]),
                }
                for segment in speaker_predictions
            ],
        },
    }


def _bootstrap_from_current_clip(output_audio_path: Path) -> tuple[str, dict[str, Any]]:
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        raise RuntimeError("No clip found at the current playhead")

    clip_context = resolve_service.get_clip_audio_context(clip)
    if clip_context is None:
        raise RuntimeError("Could not determine clip timing context")

    file_path = clip_context["file_path"]
    full_audio = extract_audio(file_path)
    audio_path = output_audio_path
    if clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
        extracted = extract_audio_segment(
            full_audio,
            float(clip_context["source_start_seconds"]),
            float(clip_context["source_end_seconds"]),
            output_path=str(output_audio_path),
        )
        audio_path = Path(extracted)
    else:
        audio_path.write_bytes(Path(full_audio).read_bytes())

    metadata = {
        "source_mode": "clip_at_playhead",
        "file_path": file_path,
        "timeline_name": resolve_service.get_current_timeline().GetName(),
        "fps": float(clip_context["fps"]),
        "seconds": {
            "clip_timeline_offset": float(clip_context["clip_timeline_offset"]),
            "clip_duration_seconds": float(clip_context["clip_duration_seconds"]),
            "source_start_seconds": clip_context.get("source_start_seconds"),
            "source_end_seconds": clip_context.get("source_end_seconds"),
        },
    }
    return str(audio_path), metadata


def _bootstrap_from_timeline_range(output_audio_path: Path) -> tuple[str, dict[str, Any]]:
    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        raise RuntimeError("No active timeline")

    in_out = resolve_service.get_in_out_range()
    if not in_out:
        raise RuntimeError("No timeline in/out range is set")

    range_in = float(in_out["in_seconds"])
    range_out = float(in_out["out_seconds"])
    if range_out <= range_in:
        raise RuntimeError("Timeline in/out range is invalid")

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())
    timeline_duration_seconds = (timeline_end_frame - timeline_start_frame) / fps

    clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        raise RuntimeError("No clips found in the current timeline")

    audio_cache: dict[str, str] = {}
    clip_segments: list[dict[str, Any]] = []
    for clip in clips:
        clip_context = resolve_service.get_clip_audio_context(clip)
        if clip_context is None:
            continue

        clip_start = float(clip_context["clip_timeline_offset"])
        clip_end = clip_start + float(clip_context["clip_duration_seconds"])
        if clip_end <= range_in or clip_start >= range_out:
            continue

        file_path = str(clip_context["file_path"])
        if file_path not in audio_cache:
            audio_cache[file_path] = extract_audio(file_path)
        full_audio = audio_cache[file_path]

        visible_audio = full_audio
        if clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
            visible_audio = extract_audio_segment(
                full_audio,
                float(clip_context["source_start_seconds"]),
                float(clip_context["source_end_seconds"]),
            )

        clip_segments.append(
            {
                "audio_path": visible_audio,
                "timeline_start_seconds": clip_start,
                "duration_seconds": float(clip_context["clip_duration_seconds"]),
            }
        )

    if not clip_segments:
        raise RuntimeError("No timeline clips overlap the current in/out range")

    assembled_audio = assemble_timeline_audio(clip_segments, timeline_duration_seconds)
    excerpt = extract_audio_segment(
        assembled_audio,
        range_in,
        range_out,
        output_path=str(output_audio_path),
    )
    metadata = {
        "source_mode": "timeline_in_out_range",
        "timeline_name": timeline.GetName(),
        "fps": fps,
        "range": in_out,
    }
    return excerpt, metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", default=None, help="Optional fixture id")
    parser.add_argument("--language", default=None, help="Optional transcription language")
    return parser.parse_args()


def bootstrap_current_resolve_fixture(
    fixture_id: str | None = None,
    language: str | None = None,
) -> dict[str, str]:
    if not resolve_service.connected and not resolve_service.try_connect():
        raise RuntimeError("Could not connect to DaVinci Resolve")
    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        raise RuntimeError("No active timeline")

    generated_dir = Path("evals/generated")
    fixtures_dir = Path("evals/fixtures")
    generated_dir.mkdir(parents=True, exist_ok=True)
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    timeline_name = timeline.GetName() or "timeline"
    range_info = resolve_service.get_in_out_range()
    source_tag = "range" if range_info else "clip"
    fixture_id = fixture_id or f"{_slugify(timeline_name)}_{source_tag}"

    audio_path = generated_dir / f"{fixture_id}.wav"
    transcript_path = generated_dir / f"{fixture_id}.transcript.json"
    predictions_path = generated_dir / f"{fixture_id}.predictions.json"
    fixture_path = fixtures_dir / f"{fixture_id}.json"

    if range_info:
        excerpt_audio_path, metadata = _bootstrap_from_timeline_range(audio_path)
    else:
        excerpt_audio_path, metadata = _bootstrap_from_current_clip(audio_path)

    transcript = audio_service.transcribe(excerpt_audio_path, language=language, use_cache=False)
    filler_predictions = [
        {
            "start": round(float(segment["start"]), 4),
            "end": round(float(segment["end"]), 4),
            "kind": segment.get("kind") or "filler",
        }
        for segment in audio_service.analyze_filler_candidates(excerpt_audio_path, transcript)
    ]
    filler_predictions.sort(key=lambda segment: (segment["start"], segment["end"]))

    fps = float(metadata["fps"])
    silence_predictions = _detect_silence_predictions(excerpt_audio_path, fps)
    speaker_segments, method, fallback_reason = diarization_service.diarize_segments(excerpt_audio_path)
    annotated_words = diarization_service.assign_speakers_to_words(
        copy.deepcopy(transcript.get("words", [])),
        speaker_segments,
    )

    _save_json(transcript_path, transcript)
    _save_json(
        predictions_path,
        {
            "metadata": {
                **metadata,
                "diarization_method": method,
                "diarization_fallback_reason": fallback_reason,
            },
            "filler_predictions": filler_predictions,
            "silence_predictions": silence_predictions,
            "speaker_segments": speaker_segments,
            "annotated_words": annotated_words,
        },
    )

    fixture_payload = _build_fixture_payload(
        fixture_id=fixture_id,
        audio_rel_path=str(Path("..") / "generated" / audio_path.name),
        transcript_rel_path=str(Path("..") / "generated" / transcript_path.name),
        predictions_rel_path=str(Path("..") / "generated" / predictions_path.name),
        fps=fps,
        language=language or transcript.get("language"),
        notes=(
            f"Bootstrapped from Resolve {metadata['source_mode']} on timeline '{timeline_name}'. "
            "Expected values are placeholders copied from the current model output."
        ),
        filler_predictions=filler_predictions,
        silence_predictions=silence_predictions,
        speaker_predictions=speaker_segments,
    )
    _save_json(fixture_path, fixture_payload)

    return {
        "fixture_path": str(fixture_path.resolve()),
        "audio_path": str(Path(excerpt_audio_path).resolve()),
        "transcript_path": str(transcript_path.resolve()),
        "predictions_path": str(predictions_path.resolve()),
    }


def _bootstrap_via_backend(args: argparse.Namespace) -> int:
    import httpx

    base_url = f"http://{settings.host}:{settings.port}"
    response = httpx.post(
        f"{base_url}/api/evals/bootstrap",
        json={"id": args.id, "language": args.language},
        timeout=600.0,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    print(f"Created starter fixture: {payload['fixture_path']}")
    print(f"Audio excerpt: {payload['audio_path']}")
    print(f"Transcript: {payload['transcript_path']}")
    print(f"Predictions: {payload['predictions_path']}")
    print("Fixture is disabled by default until you review and correct the placeholder expectations.")
    return 0


def main() -> int:
    args = parse_args()
    try:
        result = bootstrap_current_resolve_fixture(args.id, args.language)
    except Exception as local_error:
        try:
            return _bootstrap_via_backend(args)
        except Exception:
            raise local_error

    print(f"Created starter fixture: {result['fixture_path']}")
    print(f"Audio excerpt: {result['audio_path']}")
    print(f"Transcript: {result['transcript_path']}")
    print(f"Predictions: {result['predictions_path']}")
    print("Fixture is disabled by default until you review and correct the placeholder expectations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
