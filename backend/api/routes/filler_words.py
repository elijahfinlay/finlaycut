"""Filler word detection and removal endpoints."""

import json
import logging
import os
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.cut_refiner_service import cut_refiner_service
from services.audio_state import get_last_transcription_audio, unpack_last_transcription_audio
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from utils.timeline_audio import assemble_current_timeline_audio, prepare_timeline_analysis_audio
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.filler_words")

router = APIRouter()
FILLER_NEXT_WORD_GUARD_FRAMES = 1
FILLER_PREVIEW_MARKER_NAMESPACE = "finlaycut:filler-preview"
FILLER_PREVIEW_MARKER_IN_COLOR = "Cyan"
FILLER_PREVIEW_MARKER_OUT_COLOR = "Rose"


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
    }


def _adjust_filler_cut_frame_range(
    start_frame: int,
    end_frame: int,
) -> tuple[int, int] | None:
    span_frames = int(end_frame) - int(start_frame)
    if span_frames <= 0:
        return None

    return int(start_frame), int(end_frame)


def _debug_enabled() -> bool:
    return bool(getattr(settings, "debug_cut_planning", False))


def _debug(message: str):
    if _debug_enabled():
        logger.debug(message)


def _coerce_optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _set_timeline_detection_frames(detection: dict, timeline_start_frame: int, fps: float) -> None:
    for time_key, frame_key in (
        ("start", "start_frame"),
        ("end", "end_frame"),
        ("prev_word_end", "prev_word_end_frame"),
        ("next_word_start", "next_word_start_frame"),
    ):
        value = _coerce_optional_float(detection.get(time_key))
        if value is None:
            continue
        detection[frame_key] = int(timeline_start_frame) + int(round(value * float(fps)))


def _offset_local_detection_fields(
    detection: dict,
    clip_timeline_offset: float,
    clip_start_frame: int,
    fps: float,
) -> None:
    for time_key in ("start", "end", "prev_word_end", "next_word_start"):
        value = _coerce_optional_float(detection.get(time_key))
        if value is None:
            continue
        detection[time_key] = value + float(clip_timeline_offset)

    for time_key, frame_key in (
        ("start", "start_frame"),
        ("end", "end_frame"),
        ("prev_word_end", "prev_word_end_frame"),
        ("next_word_start", "next_word_start_frame"),
    ):
        local_value = _coerce_optional_float(detection.get(time_key))
        if local_value is None:
            continue
        detection[frame_key] = int(clip_start_frame) + int(round((local_value - float(clip_timeline_offset)) * float(fps)))


def _is_previewable_filler_detection(detection: dict) -> bool:
    word = str(detection.get("word") or "").strip()
    kind = str(detection.get("kind") or "").strip()
    if word == "[silence]" or kind == "silence":
        return False
    return kind in {"", "filler", "audio_hesitation"}


def _resolve_detection_frame_range(
    detection: dict,
    timeline_start_frame: int,
    timeline_end_frame: int,
    fps: float,
) -> tuple[int, int] | None:
    start_frame = _coerce_optional_int(detection.get("start_frame"))
    end_frame = _coerce_optional_int(detection.get("end_frame"))

    if start_frame is None:
        start_seconds = _coerce_optional_float(detection.get("start"))
        if start_seconds is None:
            return None
        start_frame = int(timeline_start_frame) + int(round(start_seconds * float(fps)))

    if end_frame is None:
        end_seconds = _coerce_optional_float(detection.get("end"))
        if end_seconds is None:
            return None
        end_frame = int(timeline_start_frame) + int(round(end_seconds * float(fps)))

    if end_frame <= start_frame:
        end_frame = int(start_frame) + 1

    return _clamp_filler_cut_frame_range(
        int(start_frame),
        int(end_frame),
        detection,
        timeline_start_frame,
        timeline_end_frame,
        fps,
    )


def _build_filler_preview_timeline_markers(
    detection: dict,
    detection_index: int,
    timeline_start_frame: int,
    timeline_end_frame: int,
    fps: float,
) -> list[dict]:
    frame_range = _resolve_detection_frame_range(
        detection,
        timeline_start_frame,
        timeline_end_frame,
        fps,
    )
    if frame_range is None:
        return []

    start_frame, end_frame = frame_range
    word = str(detection.get("word") or "").strip()
    display_word = "filler" if not word or word == "[filler]" else word
    context = str(detection.get("context") or "").strip()
    duration_seconds = max(0.0, float(end_frame - start_frame) / float(fps))
    shared_note = f'Review before cutting. Word: "{display_word}". Duration: {duration_seconds:.2f}s.'
    if context:
        shared_note = f"{shared_note}\nContext: {context}"

    range_payload = {
        "version": 1,
        "feature": "filler_preview",
        "kind": "filler",
        "word": display_word,
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "detection_index": int(detection_index),
    }
    out_frame = max(int(start_frame), int(end_frame) - 1)
    return [
        {
            "frame_id": int(start_frame),
            "duration": 1,
            "color": FILLER_PREVIEW_MARKER_IN_COLOR,
            "name": f"Filler IN - {display_word}",
            "note": f"Start of filler review range.\n{shared_note}",
            "custom_data": json.dumps(
                {**range_payload, "marker_side": "in"},
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
        {
            "frame_id": out_frame,
            "duration": 1,
            "color": FILLER_PREVIEW_MARKER_OUT_COLOR,
            "name": f"Filler OUT - {display_word}",
            "note": f"End of filler review range.\n{shared_note}",
            "custom_data": json.dumps(
                {**range_payload, "marker_side": "out"},
                separators=(",", ":"),
                sort_keys=True,
            ),
        },
    ]


def _build_filler_preview_item_markers(
    detection: dict,
    detection_index: int,
    timeline_start_frame: int,
    timeline_end_frame: int,
    fps: float,
    candidate_records: list[dict],
) -> list[dict]:
    frame_range = _resolve_detection_frame_range(
        detection,
        timeline_start_frame,
        timeline_end_frame,
        fps,
    )
    if frame_range is None:
        return []

    start_frame, end_frame = frame_range
    word = str(detection.get("word") or "").strip()
    display_word = "filler" if not word or word == "[filler]" else word
    context = str(detection.get("context") or "").strip()
    duration_seconds = max(0.0, float(end_frame - start_frame) / float(fps))
    shared_note = f'Review before cutting. Word: "{display_word}". Duration: {duration_seconds:.2f}s.'
    if context:
        shared_note = f"{shared_note}\nContext: {context}"

    video_records = [
        record for record in candidate_records
        if str(record.get("media_type") or "") == "video"
    ]
    fallback_records = video_records or candidate_records
    markers: list[dict] = []
    seen_items: set[int] = set()

    for record in fallback_records:
        item = record.get("item")
        if item is None:
            continue

        item_key = id(item)
        if item_key in seen_items:
            continue
        seen_items.add(item_key)

        clip_start_frame = _coerce_optional_int(record.get("clip_start_frame"))
        clip_end_frame = _coerce_optional_int(record.get("clip_end_frame"))
        if clip_start_frame is None or clip_end_frame is None:
            continue

        overlap_start = max(int(start_frame), int(clip_start_frame))
        overlap_end = min(int(end_frame), int(clip_end_frame))
        if overlap_end <= overlap_start:
            continue

        local_start_frame = int(overlap_start) - int(clip_start_frame)
        local_end_frame = max(int(local_start_frame), local_start_frame + max(1, int(overlap_end) - int(overlap_start)) - 1)
        local_duration = max(1, int(overlap_end) - int(overlap_start))
        source_start_frame = _coerce_optional_int(record.get("source_start_frame"))
        source_frame_base = source_start_frame + local_start_frame if source_start_frame is not None else local_start_frame
        source_end_frame = source_frame_base + local_duration - 1
        media_type = str(record.get("media_type") or "clip")
        track_index = _coerce_optional_int(record.get("track_index")) or 1
        clip_name = resolve_service.get_timeline_item_name(item)
        range_payload = {
            "version": 1,
            "feature": "filler_preview",
            "kind": "filler_clip_range",
            "word": display_word,
            "timeline_start_frame": int(start_frame),
            "timeline_end_frame": int(end_frame),
            "local_start_frame": int(local_start_frame),
            "local_end_frame": int(local_end_frame),
            "detection_index": int(detection_index),
            "media_type": media_type,
            "track_index": int(track_index),
            "clip_name": clip_name,
            "timeline_item_unique_id": resolve_service.get_timeline_item_unique_id(item),
        }
        markers.extend([
            {
                "item": item,
                "frame_id": int(source_frame_base),
                "duration": 1,
                "color": FILLER_PREVIEW_MARKER_IN_COLOR,
                "name": f"Filler IN - {display_word}",
                "note": f"Start of filler review range on clip.\n{shared_note}",
                "custom_data": json.dumps(
                    {**range_payload, "marker_side": "in"},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
            {
                "item": item,
                "frame_id": int(source_end_frame),
                "duration": 1,
                "color": FILLER_PREVIEW_MARKER_OUT_COLOR,
                "name": f"Filler OUT - {display_word}",
                "note": f"End of filler review range on clip.\n{shared_note}",
                "custom_data": json.dumps(
                    {**range_payload, "marker_side": "out"},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ])

    return markers


def _clamp_filler_cut_frame_range(
    start_frame: int,
    end_frame: int,
    filler: dict,
    timeline_start_frame: int,
    timeline_end_frame: int,
    fps: float,
) -> tuple[int, int] | None:
    clamped_start = int(start_frame)
    clamped_end = int(end_frame)

    prev_word_end_frame = filler.get("prev_word_end_frame")
    if prev_word_end_frame is None:
        prev_word_end = _coerce_optional_float(filler.get("prev_word_end"))
        if prev_word_end is not None:
            prev_word_end_frame = int(timeline_start_frame) + int(round(prev_word_end * float(fps)))
    if prev_word_end_frame is not None:
        clamped_start = max(clamped_start, int(prev_word_end_frame))

    next_word_start_frame = filler.get("next_word_start_frame")
    if next_word_start_frame is None:
        next_word_start = _coerce_optional_float(filler.get("next_word_start"))
        if next_word_start is not None:
            next_word_start_frame = int(timeline_start_frame) + int(round(next_word_start * float(fps)))
    if next_word_start_frame is not None:
        clamped_end = min(clamped_end, int(next_word_start_frame) - int(FILLER_NEXT_WORD_GUARD_FRAMES))

    clamped_start = max(int(timeline_start_frame), min(int(timeline_end_frame), clamped_start))
    clamped_end = max(int(timeline_start_frame), min(int(timeline_end_frame), clamped_end))

    if clamped_end <= clamped_start:
        return None

    return clamped_start, clamped_end


class FillerAnalyzeRequest(BaseModel):
    language: Optional[str] = None
    filler_words: Optional[list[str]] = None
    transcript: Optional[dict] = None  # Pass existing transcript to skip re-transcription


@router.post("/analyze")
@heavy_operation("Filler Word Analysis")
async def analyze_filler_words(request: FillerAnalyzeRequest = FillerAnalyzeRequest()):
    """Detect filler words. Uses provided transcript if available, otherwise transcribes."""

    # If an existing transcript with words was provided, skip transcription entirely
    if request.transcript and request.transcript.get("words"):
        try:
            timeline = resolve_service.get_current_timeline()
            if timeline is None:
                return {"error": "No active timeline"}
            fps_str = timeline.GetSetting("timelineFrameRate")
            fps = float(fps_str) if fps_str else 24.0
            timeline_start_frame = int(timeline.GetStartFrame())
            signature_info = resolve_service.get_timeline_signature()
            current_timeline_signature = str(signature_info.get("timeline_signature") or "") if signature_info else ""

            transcript_words = request.transcript.get("words") or []
            transcript_start = min(
                (float(word.get("start", 0.0)) for word in transcript_words),
                default=0.0,
            )
            transcript_end = max(
                (float(word.get("end", word.get("start", 0.0))) for word in transcript_words),
                default=0.0,
            )

            # The cached audio may have an offset if it's a single-clip extraction
            # (word timestamps are in timeline seconds, audio starts at offset=0).
            # We need to subtract the offset from word times when analysing audio.
            audio_offset = 0.0
            assembled_audio = None
            analysis_audio_source: str | None = None
            analysis_audio_fallback_reason: str | None = None
            if bool(getattr(settings, "enable_master_mix_analysis_default", True)):
                cached = unpack_last_transcription_audio(get_last_transcription_audio())
                if cached and os.path.exists(cached[0]):
                    cached_path, cached_duration, cached_offset, cached_signature, _cached_covers_full_timeline, cached_source = cached
                    cache_start = float(cached_offset)
                    cache_end = cache_start + float(cached_duration)
                    signature_matches = bool(current_timeline_signature) and cached_signature == current_timeline_signature
                    covers_transcript = (
                        transcript_start >= cache_start - 0.05
                        and transcript_end <= cache_end + 0.05
                    )
                    if signature_matches and covers_transcript and cached_source in {"cached_master_mix", "rendered_master_mix"}:
                        assembled_audio = cached_path
                        audio_offset = 0.0
                        analysis_audio_source = "cached_master_mix"
                        await manager.send_progress("filler_words", 0.34, "Using rendered timeline mix...")
                        logger.info(
                            "Filler analysis using cached master-mix transcription audio (duration=%.2fs, transcript_span=%.2fs-%.2fs)",
                            cached_duration,
                            transcript_start,
                            transcript_end,
                        )

                if assembled_audio is None:
                    analysis_audio = prepare_timeline_analysis_audio(
                        name_prefix="finlaycut_filler_analysis",
                        fallback_restore_page="edit",
                        allow_track_fallback=True,
                        reuse_cached_master_mix=True,
                    )
                    assembled_audio = analysis_audio.get("audio_path")
                    if not assembled_audio:
                        return {"error": "Could not prepare current timeline audio"}
                    analysis_audio_source = str(analysis_audio.get("analysis_audio_source") or "rendered_master_mix")
                    analysis_audio_fallback_reason = (
                        str(analysis_audio.get("analysis_audio_fallback_reason"))
                        if analysis_audio.get("analysis_audio_fallback_reason") is not None
                        else None
                    )
                    fps = float(analysis_audio.get("fps") or fps)
                    timeline_start_frame = int(analysis_audio.get("timeline_start_frame") or timeline_start_frame)
                    audio_offset = 0.0
                    await manager.send_progress(
                        "filler_words",
                        0.34,
                        "Using rendered timeline mix..."
                        if analysis_audio_source in {"cached_master_mix", "rendered_master_mix"}
                        else "Using best-track fallback audio...",
                    )
            else:
                cached = unpack_last_transcription_audio(get_last_transcription_audio())
                if cached and os.path.exists(cached[0]):
                    cached_path, cached_duration, cached_offset, cached_signature, _cached_covers_full_timeline, cached_source = cached
                    cache_start = float(cached_offset)
                    cache_end = cache_start + float(cached_duration)
                    signature_matches = bool(current_timeline_signature) and cached_signature == current_timeline_signature
                    covers_transcript = (
                        transcript_start >= cache_start - 0.05
                        and transcript_end <= cache_end + 0.05
                    )
                    if signature_matches and covers_transcript:
                        assembled_audio = cached_path
                        audio_offset = cached_offset
                        analysis_audio_source = cached_source or (
                            "assembled_best_track" if abs(float(cached_offset)) <= 0.05 else "per_clip_best_track"
                        )
                        analysis_audio_fallback_reason = (
                            None if analysis_audio_source in {"cached_master_mix", "rendered_master_mix"} else "master_mix_disabled"
                        )
                        await manager.send_progress("filler_words", 0.34, "Using transcription audio...")
                        logger.info(
                            "Filler analysis using cached transcription audio (duration=%.2fs, offset=%.2fs, transcript_span=%.2fs-%.2fs)",
                            cached_duration,
                            cached_offset,
                            transcript_start,
                            transcript_end,
                        )

                if assembled_audio is None:
                    await manager.send_progress("filler_words", 0.34, "Assembling timeline audio...")
                    assembled_audio, assembled_fps, assembled_timeline_start_frame, _timeline_end_frame = assemble_current_timeline_audio()
                    if not assembled_audio or assembled_fps is None or assembled_timeline_start_frame is None:
                        return {"error": "Could not assemble current timeline audio"}
                    fps = float(assembled_fps)
                    timeline_start_frame = int(assembled_timeline_start_frame)
                    audio_offset = 0.0
                    analysis_audio_source = "assembled_best_track"
                    analysis_audio_fallback_reason = "master_mix_disabled"
                    logger.info(
                        "Filler analysis fell back to assembled timeline audio (transcript_end=%.2fs)",
                        transcript_end,
                    )

            # If there's an audio offset, shift word timestamps to audio-local time
            # so that audio analysis functions look at the correct positions.
            analysis_transcript = request.transcript
            if audio_offset > 0.0:
                shifted_words = []
                for w in transcript_words:
                    sw = dict(w)
                    sw["start"] = float(sw.get("start", 0.0)) - audio_offset
                    sw["end"] = float(sw.get("end", 0.0)) - audio_offset
                    shifted_words.append(sw)
                analysis_transcript = {"words": shifted_words}
                logger.info(
                    "Shifted %d word timestamps by -%.2fs for audio-local analysis",
                    len(shifted_words),
                    audio_offset,
                )

            await manager.send_progress("filler_words", 0.58, "Detecting and refining fillers...")
            profile = audio_service.build_waveform_profile(assembled_audio)
            detections = audio_service.analyze_filler_candidates(
                assembled_audio,
                analysis_transcript,
                filler_words=request.filler_words,
                profile=profile,
            )

            # Shift detection timestamps back to timeline seconds before computing frames
            if audio_offset > 0.0:
                for detection in detections:
                    for key in ("start", "end", "prev_word_end", "next_word_start"):
                        val = _coerce_optional_float(detection.get(key))
                        if val is not None:
                            detection[key] = val + audio_offset

            for detection in detections:
                _set_timeline_detection_frames(detection, timeline_start_frame, fps)
            logger.info(
                "Filler analysis complete: transcript_words=%d detections=%d audio_offset=%.2f",
                len(transcript_words),
                len(detections),
                audio_offset,
            )
        except Exception as e:
            logger.warning("Filler analysis skipped: %s", e)
            await manager.send_error("filler_words", str(e))
            return {"error": str(e)}

        await manager.send_progress("filler_words", 1.0, f"Found {len(detections)} filler candidates")
        return {
            "detections": detections,
            "count": len(detections),
            "analysis_audio_source": analysis_audio_source,
            "analysis_audio_fallback_reason": analysis_audio_fallback_reason,
        }

    # No transcript provided — transcribe from timeline
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("filler_words", 0.02, "Extracting audio...")

        clip_context = resolve_service.get_clip_audio_context(clip)
        if clip_context is None:
            return {"error": "Could not determine clip timing context"}
        fps = clip_context["fps"]
        timeline_start_frame = clip_context["timeline_start_frame"]
        clip_start_frame = clip_context["clip_start_frame"]
        clip_timeline_offset = clip_context["clip_timeline_offset"]
        source_range = None
        if clip_context.get("source_start_frame") is not None and clip_context.get("source_end_frame") is not None:
            source_range = {
                "source_start_frame": int(clip_context["source_start_frame"]),
                "source_end_frame": int(clip_context["source_end_frame"]),
            }
        _debug(
            f"analyze clip context: clip_start={clip.GetStart()}, clip_end={clip.GetEnd()}, "
            f"timeline_start={timeline_start_frame}, offset_s={clip_timeline_offset:.3f}"
        )

        full_audio = extract_audio(file_path)
        audio_path = full_audio
        if clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
            try:
                audio_path = extract_audio_segment(
                    full_audio,
                    clip_context["source_start_seconds"],
                    clip_context["source_end_seconds"],
                )
            except Exception as e:
                logger.warning("Segment extraction failed (%s), using full audio", e)

        # Use async transcribe with real progress (0.05 - 0.85 range)
        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.80
            await manager.send_progress("filler_words", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path,
            language=request.language,
            progress_fn=progress_fn,
        )

        await manager.send_progress("filler_words", 0.88, "Detecting and refining filler words...")
        profile = audio_service.build_waveform_profile(audio_path)
        detections = audio_service.analyze_filler_candidates(
            audio_path,
            transcript,
            filler_words=request.filler_words,
            profile=profile,
        )

        for w in transcript.get("words", []):
            local_start = float(w.get("start", 0.0))
            local_end = float(w.get("end", local_start))
            w["start"] = local_start + clip_timeline_offset
            w["end"] = local_end + clip_timeline_offset
            w["start_frame"] = clip_start_frame + int(round(local_start * fps))
            w["end_frame"] = clip_start_frame + int(round(local_end * fps))
            if source_range is not None:
                w["source_start_frame"] = int(source_range["source_start_frame"]) + int(round(local_start * fps))
                w["source_end_frame"] = int(source_range["source_start_frame"]) + int(round(local_end * fps))

        for detection in detections:
            local_start = float(detection["start"])
            local_end = float(detection["end"])
            _offset_local_detection_fields(detection, clip_timeline_offset, clip_start_frame, fps)
            if source_range is not None:
                detection["source_start_frame"] = int(source_range["source_start_frame"]) + int(round(local_start * fps))
                detection["source_end_frame"] = int(source_range["source_start_frame"]) + int(round(local_end * fps))

        _debug(
            f"analyze detections: count={len(detections)}, "
            f"sample={detections[:settings.debug_log_limit]}"
        )

        await manager.send_progress("filler_words", 1.0, f"Found {len(detections)} filler words")

        return {
            "detections": detections,
            "count": len(detections),
            "transcript": transcript,
            "analysis_audio_source": "per_clip_best_track",
            "analysis_audio_fallback_reason": (
                "master_mix_disabled" if not bool(getattr(settings, "enable_master_mix_analysis_default", True)) else None
            ),
        }
    except Exception as e:
        await manager.send_error("filler_words", str(e))
        return {"error": str(e)}


@router.post("/preview-markers")
async def preview_filler_word_markers(detections: list[dict]):
    """Refresh non-destructive Resolve markers for detected filler spans."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    selected = [detection for detection in detections if detection.get("selected", True)]
    previewable = [detection for detection in selected if _is_previewable_filler_detection(detection)]
    if not previewable:
        clear_result = resolve_service.refresh_managed_markers(FILLER_PREVIEW_MARKER_NAMESPACE)
        if clear_result.get("error"):
            return {"error": clear_result["error"]}
        cleared_count = int(clear_result.get("deleted_count") or 0)
        return {
            "success": True,
            "count": 0,
            "cleared_count": cleared_count,
            "message": (
                f"Cleared {cleared_count} filler review markers"
                if cleared_count
                else "No filler review markers to clear"
            ),
        }

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())
    item_records = resolve_service._get_timeline_item_records(media_types=("video", "audio"))

    timeline_markers: list[dict] = []
    timeline_item_markers: list[dict] = []
    skipped_invalid = 0
    for detection_index, detection in enumerate(previewable):
        detection_timeline_markers = _build_filler_preview_timeline_markers(
            detection,
            detection_index=detection_index,
            timeline_start_frame=timeline_start_frame,
            timeline_end_frame=timeline_end_frame,
            fps=fps,
        )
        if not detection_timeline_markers:
            skipped_invalid += 1
            continue
        timeline_markers.extend(detection_timeline_markers)

        frame_range = _resolve_detection_frame_range(
            detection,
            timeline_start_frame,
            timeline_end_frame,
            fps,
        )
        if frame_range is None:
            continue
        start_frame, end_frame = frame_range
        overlapping_records = [
            record for record in item_records
            if _coerce_optional_int(record.get("clip_start_frame")) is not None
            and _coerce_optional_int(record.get("clip_end_frame")) is not None
            and int(record["clip_end_frame"]) > int(start_frame)
            and int(record["clip_start_frame"]) < int(end_frame)
        ]
        timeline_item_markers.extend(
            _build_filler_preview_item_markers(
                detection,
                detection_index=detection_index,
                timeline_start_frame=timeline_start_frame,
                timeline_end_frame=timeline_end_frame,
                fps=fps,
                candidate_records=overlapping_records,
            )
        )

    if not timeline_markers:
        return {"error": "Could not build preview markers for the selected filler words"}

    result = resolve_service.refresh_managed_markers(
        FILLER_PREVIEW_MARKER_NAMESPACE,
        timeline_markers=timeline_markers,
        timeline_item_markers=timeline_item_markers,
    )
    if result.get("error"):
        return {"error": result["error"]}

    applied_count = int(result.get("applied_count") or 0)
    failed_count = len(result.get("failed_frames") or []) + len(result.get("timeline_item_failed_frames") or [])
    if failed_count:
        return {
            "error": (
                f"Placed {applied_count} of {len(timeline_markers) + len(timeline_item_markers)} filler preview markers. "
                "Some markers could not be added, likely because another marker already occupies that frame."
            )
        }

    timeline_name = timeline.GetName() if hasattr(timeline, "GetName") else None
    preview_count = len(previewable) - skipped_invalid
    clip_marker_count = int(result.get("timeline_item_applied_count") or 0)
    message = f"Updated {preview_count} filler review ranges"
    if timeline_name:
        message = f"{message} on {timeline_name}"
    if skipped_invalid:
        message = f"{message} ({skipped_invalid} skipped)"

    return {
        "success": True,
        "count": preview_count,
        "cleared_count": int(result.get("deleted_count") or 0),
        "timeline_marker_count": int(result.get("timeline_applied_count") or 0),
        "clip_marker_count": clip_marker_count,
        "skipped_invalid": skipped_invalid,
        "message": message,
    }


@router.post("/apply")
async def apply_filler_word_removal(detections: list[dict]):
    """Remove filler words from the timeline by cutting them out."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    selected = [d for d in detections if d.get("selected", True)]
    if not selected:
        return {"error": "No filler words selected for removal"}

    _debug(
        f"apply called: detections={len(detections)}, selected={len(selected)}"
    )

    await manager.send_progress("filler_words", 0.1, "Creating backup...")

    # Create backup (non-blocking — proceed even if backup fails)
    backup = resolve_service.duplicate_timeline()
    backup_name = "none"
    if backup is not None:
        try:
            backup_name = backup.GetName()
        except Exception:
            backup_name = "backup"
    else:
        logger.warning("Could not create backup timeline")

    await manager.send_progress("filler_words", 0.3, "Building edit plan...")

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    tl_start = timeline.GetStartFrame()
    tl_end = timeline.GetEndFrame()
    total_duration = (tl_end - tl_start) / fps

    debug_payload = {
        "total_detections": len(detections),
        "selected_detections": len(selected),
        "timeline_start_frame": int(tl_start),
        "timeline_end_frame": int(tl_end),
        "timeline_duration_s": round(total_duration, 3),
        "fps": fps,
    }

    await manager.send_progress("filler_words", 0.3, "Rendering precision timeline audio...")
    refinement = cut_refiner_service.refine_cut_candidates(
        [
            {
                **detection,
                "kind": detection.get("kind") or "filler",
            }
            for detection in selected
        ],
        name_prefix="finlaycut_filler_apply",
    )
    if refinement.get("error"):
        return {"error": refinement["error"]}

    cut_frame_ranges = list(refinement["cut_frame_ranges"])
    kept_segments = list(refinement["kept_segments"])
    applied_cut_segments = list(refinement["applied_cut_segments"])
    _debug(
        f"timeline duration={total_duration:.3f}s, refined_sample={applied_cut_segments[:settings.debug_log_limit]}"
    )

    sanitized_fillers = [
        {
            "word": segment.get("word"),
            "start": round(float(segment.get("start", 0.0)), 3),
            "end": round(float(segment.get("end", 0.0)), 3),
            "start_frame": int(segment["start_frame"]),
            "end_frame": int(segment["end_frame"]),
            "duration": round(float(segment.get("duration", 0.0)), 3),
            "boundary_source": segment.get("boundary_source"),
            "kind": segment.get("kind"),
        }
        for segment in applied_cut_segments
        if segment.get("start_frame") is not None and segment.get("end_frame") is not None
    ]
    skipped_invalid = max(0, len(selected) - len(sanitized_fillers))

    debug_payload["sanitized_selected"] = len(sanitized_fillers)
    debug_payload["skipped_invalid"] = skipped_invalid
    debug_payload["sanitized_sample"] = sanitized_fillers[:settings.debug_log_limit]
    debug_payload["kept_segment_count"] = len(kept_segments)
    debug_payload["kept_segments_sample"] = kept_segments[:settings.debug_log_limit]
    debug_payload["raw_cut_frame_range_count"] = len(cut_frame_ranges)
    debug_payload["cut_frame_range_count"] = len(cut_frame_ranges)
    _debug(
        f"kept segments={len(kept_segments)}, skipped_invalid={skipped_invalid}, "
        f"kept_sample={debug_payload['kept_segments_sample']}"
    )

    if not kept_segments:
        response = {"error": "Nothing left after removing all filler words"}
        if _debug_enabled():
            response["debug"] = debug_payload
        return response

    await manager.send_progress("filler_words", 0.5, "Applying edits to current timeline...")

    result = resolve_service.rebuild_timeline_keeping(kept_segments, in_place=True)

    if not result["success"]:
        response = {"error": result.get("error", "Failed to apply edits to current timeline")}
        if _debug_enabled():
            debug_payload["rebuild_debug"] = result.get("debug")
            response["debug"] = debug_payload
        return response

    total_removed = sum((end_frame - start_frame) / fps for start_frame, end_frame in cut_frame_ranges)

    await manager.send_progress("filler_words", 1.0, "Done")
    await manager.send_complete("filler_words", {
        "removed_count": len(cut_frame_ranges),
        "total_removed": round(total_removed, 1)
    })

    from api.routes.undo import _record
    undo_entry = _record("Filler Word Removal", backup_name,
            f"Removed {len(cut_frame_ranges)} filler sections ({total_removed:.1f}s)")

    response = {
        "success": True,
        "removed_count": len(cut_frame_ranges),
        "total_removed_seconds": round(total_removed, 1),
        "backup_timeline": backup_name,
        "timeline_name": result.get("timeline_name"),
        "applied_cut_segments": applied_cut_segments,
        "undo_entry_id": undo_entry["id"],
        "message": f"Removed {len(cut_frame_ranges)} filler sections ({total_removed:.1f}s) on the current timeline. Backup: {backup_name}"
    }
    if _debug_enabled():
        debug_payload["rebuild_debug"] = result.get("debug")
        response["debug"] = debug_payload
    _debug(
        f"apply success: removed={len(sanitized_fillers)}, total_removed={total_removed:.3f}s, "
        f"new_timeline={result.get('timeline_name')}"
    )
    return response
