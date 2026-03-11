"""Shared waveform-first cut-boundary refinement for destructive edit routes."""

from __future__ import annotations

import logging
from typing import Any

from config import settings
from services.audio_service import audio_service
from utils.timeline_audio import get_precision_timeline_audio

logger = logging.getLogger("finlaycut.cut_refiner")

_FILLER_KINDS = {
    "filler",
    "audio_hesitation",
    "word_offset",
    "word_onset",
    "lexical_filler",
}
_SILENCE_KINDS = {"silence"}
_LOW_ENERGY_KINDS = {"low_prominence_speech"}
_GENERIC_KINDS = {"speech", "script", "retake"}

_LOW_ENERGY_MAX_SHIFT_MS = 40
_LOW_ENERGY_MIN_DURATION_MS = 120


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _merge_frame_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    valid = sorted((int(start), int(end)) for start, end in ranges if int(end) > int(start))
    if not valid:
        return []

    merged = [valid[0]]
    for start, end in valid[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _subtract_frame_ranges(
    base_start: int,
    base_end: int,
    cuts: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if base_end <= base_start:
        return []

    if not cuts:
        return [(base_start, base_end)]

    remaining: list[tuple[int, int]] = []
    cursor = int(base_start)
    for cut_start, cut_end in cuts:
        if cut_end <= cursor:
            continue
        if cut_start >= base_end:
            break
        if cut_start > cursor:
            remaining.append((cursor, min(cut_start, base_end)))
        cursor = max(cursor, cut_end)
        if cursor >= base_end:
            break

    if cursor < base_end:
        remaining.append((cursor, base_end))

    return remaining


def _frame_range_to_segment(start_frame: int, end_frame: int, timeline_start_frame: int, fps: float) -> dict:
    return {
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "start": (int(start_frame) - int(timeline_start_frame)) / float(fps),
        "end": (int(end_frame) - int(timeline_start_frame)) / float(fps),
        "duration": max(0.0, (int(end_frame) - int(start_frame)) / float(fps)),
    }


def _trim_segment(segment: dict, left_ms: int = 0, right_ms: int = 0) -> dict | None:
    left_seconds = max(0, int(left_ms)) / 1000.0
    right_seconds = max(0, int(right_ms)) / 1000.0
    start = float(segment.get("start", 0.0)) + left_seconds
    end = float(segment.get("end", 0.0)) - right_seconds
    if end <= start:
        return None

    updated = dict(segment)
    updated["start"] = round(start, 4)
    updated["end"] = round(end, 4)
    updated["duration"] = round(updated["end"] - updated["start"], 4)
    return updated


class CutRefinerService:
    def _canonical_kind(self, raw_kind: Any) -> str:
        kind = str(raw_kind or "").strip().lower()
        if kind in _FILLER_KINDS:
            return "filler"
        if kind in _SILENCE_KINDS:
            return "silence"
        if kind in _LOW_ENERGY_KINDS:
            return "low_prominence_speech"
        if kind == "script":
            return "script"
        if kind == "retake":
            return "retake"
        if kind in _GENERIC_KINDS:
            return kind
        return "speech"

    def _normalize_candidate(
        self,
        candidate: dict,
        timeline_start_frame: int,
        timeline_end_frame: int,
        fps: float,
        duration: float,
    ) -> dict | None:
        start_frame = _coerce_int(candidate.get("start_frame"))
        end_frame = _coerce_int(candidate.get("end_frame"))

        if start_frame is not None and end_frame is not None:
            start = (start_frame - int(timeline_start_frame)) / float(fps)
            end = (end_frame - int(timeline_start_frame)) / float(fps)
        else:
            start = _coerce_float(candidate.get("start")) or 0.0
            end = _coerce_float(candidate.get("end"))
            if end is None:
                end = start
            start_frame = int(timeline_start_frame) + int(round(start * float(fps)))
            end_frame = int(timeline_start_frame) + int(round(end * float(fps)))

        start = max(0.0, min(float(duration), float(start)))
        end = max(0.0, min(float(duration), float(end)))
        start_frame = max(int(timeline_start_frame), min(int(timeline_end_frame), int(start_frame)))
        end_frame = max(int(timeline_start_frame), min(int(timeline_end_frame), int(end_frame)))
        if end <= start or end_frame <= start_frame:
            return None

        normalized = dict(candidate)
        normalized["kind"] = self._canonical_kind(candidate.get("kind"))
        normalized["start"] = round(start, 4)
        normalized["end"] = round(end, 4)
        normalized["start_frame"] = int(start_frame)
        normalized["end_frame"] = int(end_frame)
        normalized["duration"] = round(end - start, 4)

        for time_key, frame_key in (
            ("prev_word_end", "prev_word_end_frame"),
            ("next_word_start", "next_word_start_frame"),
        ):
            value = _coerce_float(candidate.get(time_key))
            frame_value = _coerce_int(candidate.get(frame_key))
            if frame_value is not None:
                value = (int(frame_value) - int(timeline_start_frame)) / float(fps)
            if value is None:
                normalized.pop(time_key, None)
                continue
            normalized[time_key] = max(0.0, min(float(duration), value))

        return normalized

    def _policy_for_kind(self, kind: str, default_silence_padding_ms: int | None = None) -> dict[str, Any]:
        silence_padding_ms = (
            int(default_silence_padding_ms)
            if default_silence_padding_ms is not None
            else int(getattr(settings, "silence_padding_ms", 100))
        )
        if kind == "filler":
            return {
                "mode": "filler",
                "min_duration_ms": 30,
                "merge": True,
            }
        if kind == "silence":
            return {
                "mode": "silence",
                "preserve_padding_ms": silence_padding_ms,
                "min_duration_ms": 20,
                "merge": True,
            }
        if kind == "low_prominence_speech":
            return {
                "mode": "low_energy",
                "max_shift_ms": _LOW_ENERGY_MAX_SHIFT_MS,
                "min_duration_ms": _LOW_ENERGY_MIN_DURATION_MS,
                "trim_left_ms": silence_padding_ms,
                "trim_right_ms": silence_padding_ms,
                "merge": True,
            }
        if kind == "script":
            return {
                "mode": "spoken" if bool(getattr(settings, "enable_spoken_waveform_refiner", True)) else "generic",
                "max_shift_ms": 90,
                "min_duration_ms": int(getattr(settings, "spoken_refine_min_duration_ms", 30)),
                "merge": True,
            }
        if kind == "retake":
            return {
                "mode": "spoken" if bool(getattr(settings, "enable_spoken_waveform_refiner", True)) else "generic",
                "max_shift_ms": 90,
                "min_duration_ms": int(getattr(settings, "spoken_refine_min_duration_ms", 30)),
                "merge": True,
            }
        return {
            "mode": "spoken" if bool(getattr(settings, "enable_spoken_waveform_refiner", True)) else "generic",
            "max_shift_ms": 90,
            "min_duration_ms": int(getattr(settings, "spoken_refine_min_duration_ms", 30)),
            "merge": True,
        }

    def _refine_group(
        self,
        audio_path: str,
        kind: str,
        segments: list[dict],
        profile: dict[str, Any] | None,
        default_silence_padding_ms: int | None = None,
    ) -> list[dict]:
        segment_padding_ms = next(
            (
                int(padding)
                for padding in (_coerce_int(segment.get("padding_ms")) for segment in segments)
                if padding is not None
            ),
            None,
        )
        policy = self._policy_for_kind(
            kind,
            default_silence_padding_ms=segment_padding_ms if segment_padding_ms is not None else default_silence_padding_ms,
        )
        mode = policy["mode"]

        if mode == "filler":
            refined = audio_service.refine_filler_segments_to_waveform(
                audio_path,
                segments,
                min_duration_ms=policy["min_duration_ms"],
                profile=profile,
            )
        elif mode == "silence":
            refined = audio_service.refine_silence_segments_to_waveform(
                audio_path,
                segments,
                preserve_padding_ms=policy["preserve_padding_ms"],
                min_duration_ms=policy["min_duration_ms"],
                profile=profile,
            )
        elif mode == "spoken":
            refined = audio_service.refine_spoken_segments_to_waveform(
                audio_path,
                segments,
                min_duration_ms=policy["min_duration_ms"],
                profile=profile,
            )
        else:
            refined = audio_service.refine_segments_to_low_energy(
                audio_path,
                segments,
                max_shift_ms=policy["max_shift_ms"],
                min_duration_ms=policy["min_duration_ms"],
                profile=profile,
            )
            trim_left_ms = int(policy.get("trim_left_ms", 0))
            trim_right_ms = int(policy.get("trim_right_ms", 0))
            if trim_left_ms > 0 or trim_right_ms > 0:
                refined = [
                    trimmed
                    for trimmed in (_trim_segment(segment, trim_left_ms, trim_right_ms) for segment in refined)
                    if trimmed is not None
                ]

        finalized: list[dict] = []
        for segment in refined:
            updated = dict(segment)
            updated["kind"] = kind
            updated["boundary_source"] = str(updated.get("boundary_source") or "waveform")
            updated["refine_confidence"] = updated.get("refine_confidence")
            updated["refine_reason"] = str(updated.get("refine_reason") or "waveform_refine")
            updated["used_fallback_seed"] = bool(updated.get("used_fallback_seed", False))
            finalized.append(updated)

        if policy.get("merge"):
            finalized = self._merge_refined_segments(finalized)
        return finalized

    def _merge_refined_segments(self, segments: list[dict]) -> list[dict]:
        if not segments:
            return []

        ordered = sorted(
            (
                seg for seg in segments
                if _coerce_float(seg.get("end")) is not None and _coerce_float(seg.get("start")) is not None
            ),
            key=lambda seg: (float(seg["start"]), float(seg["end"])),
        )
        if not ordered:
            return []

        merged = [dict(ordered[0])]
        for seg in ordered[1:]:
            prev = merged[-1]
            if seg.get("kind") == prev.get("kind") and float(seg["start"]) <= float(prev["end"]):
                prev["start"] = round(min(float(prev["start"]), float(seg["start"])), 4)
                prev["end"] = round(max(float(prev["end"]), float(seg["end"])), 4)
                prev["duration"] = round(float(prev["end"]) - float(prev["start"]), 4)
                if seg.get("boundary_source") == "waveform":
                    prev["boundary_source"] = "waveform"
                prev_confidence = _coerce_float(prev.get("refine_confidence"))
                seg_confidence = _coerce_float(seg.get("refine_confidence"))
                if seg_confidence is not None and (prev_confidence is None or seg_confidence > prev_confidence):
                    prev["refine_confidence"] = seg_confidence
                    prev["refine_reason"] = str(seg.get("refine_reason") or prev.get("refine_reason") or "waveform_refine")
                prev["used_fallback_seed"] = bool(prev.get("used_fallback_seed", False) or seg.get("used_fallback_seed", False))
            else:
                merged.append(dict(seg))
        return merged

    def refine_cut_candidates(
        self,
        candidates: list[dict],
        *,
        name_prefix: str = "finlaycut_cut_refine",
        default_silence_padding_ms: int | None = None,
        fallback_restore_page: str = "edit",
    ) -> dict[str, Any]:
        audio_path, fps, timeline_start_frame, timeline_end_frame = get_precision_timeline_audio(
            name_prefix=name_prefix,
            fallback_restore_page=fallback_restore_page,
        )
        if not audio_path or fps is None or timeline_start_frame is None or timeline_end_frame is None:
            return {"error": "Could not prepare precision timeline audio"}

        duration = max(0.0, (int(timeline_end_frame) - int(timeline_start_frame)) / float(fps))
        normalized_candidates = [
            normalized
            for candidate in candidates
            for normalized in [
                self._normalize_candidate(
                    candidate,
                    int(timeline_start_frame),
                    int(timeline_end_frame),
                    float(fps),
                    duration,
                )
            ]
            if normalized is not None
        ]
        if not normalized_candidates:
            return {
                "audio_path": audio_path,
                "fps": float(fps),
                "timeline_start_frame": int(timeline_start_frame),
                "timeline_end_frame": int(timeline_end_frame),
                "refined_segments": [],
                "cut_frame_ranges": [],
                "kept_segments": [_frame_range_to_segment(int(timeline_start_frame), int(timeline_end_frame), int(timeline_start_frame), float(fps))],
                "applied_cut_segments": [],
            }

        profile = audio_service.build_waveform_profile(audio_path)
        refined_segments: list[dict] = []
        for kind in ("filler", "silence", "low_prominence_speech", "speech", "script", "retake"):
            group = [candidate for candidate in normalized_candidates if candidate["kind"] == kind]
            if not group:
                continue
            refined_segments.extend(
                self._refine_group(
                    audio_path,
                    kind,
                    group,
                    profile,
                    default_silence_padding_ms=default_silence_padding_ms,
                )
            )

        cut_frame_ranges = []
        applied_cut_segments = []
        for segment in self._merge_refined_segments(refined_segments):
            start_frame = int(timeline_start_frame) + int(round(float(segment["start"]) * float(fps)))
            end_frame = int(timeline_start_frame) + int(round(float(segment["end"]) * float(fps)))
            start_frame = max(int(timeline_start_frame), min(int(timeline_end_frame), start_frame))
            end_frame = max(int(timeline_start_frame), min(int(timeline_end_frame), end_frame))
            if end_frame <= start_frame:
                continue

            applied = dict(segment)
            applied["start_frame"] = int(start_frame)
            applied["end_frame"] = int(end_frame)
            applied["start"] = round((int(start_frame) - int(timeline_start_frame)) / float(fps), 4)
            applied["end"] = round((int(end_frame) - int(timeline_start_frame)) / float(fps), 4)
            applied["duration"] = round(float(applied["end"]) - float(applied["start"]), 4)
            cut_frame_ranges.append((int(start_frame), int(end_frame)))
            applied_cut_segments.append(applied)

        cut_frame_ranges = _merge_frame_ranges(cut_frame_ranges)
        kept_frame_ranges = _subtract_frame_ranges(
            int(timeline_start_frame),
            int(timeline_end_frame),
            cut_frame_ranges,
        )
        kept_segments = [
            _frame_range_to_segment(start_frame, end_frame, int(timeline_start_frame), float(fps))
            for start_frame, end_frame in kept_frame_ranges
        ]

        return {
            "audio_path": audio_path,
            "fps": float(fps),
            "timeline_start_frame": int(timeline_start_frame),
            "timeline_end_frame": int(timeline_end_frame),
            "refined_segments": refined_segments,
            "cut_frame_ranges": cut_frame_ranges,
            "kept_segments": kept_segments,
            "applied_cut_segments": applied_cut_segments,
        }


cut_refiner_service = CutRefinerService()
