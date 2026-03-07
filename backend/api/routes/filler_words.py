"""Filler word detection and removal endpoints."""

import logging
import os
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.audio_state import get_last_transcription_audio
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from utils.timeline_audio import assemble_current_timeline_audio
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.filler_words")

router = APIRouter()
FILLER_NEXT_WORD_GUARD_FRAMES = 1


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
            cached = get_last_transcription_audio()
            if cached and os.path.exists(cached[0]):
                cached_path, cached_duration, cached_offset, cached_signature, _cached_covers_full_timeline = cached
                cache_start = float(cached_offset)
                cache_end = cache_start + float(cached_duration)
                signature_matches = (
                    not current_timeline_signature
                    or (cached_signature is not None and cached_signature == current_timeline_signature)
                )
                covers_transcript = (
                    transcript_start >= cache_start - 0.05
                    and transcript_end <= cache_end + 0.05
                )
                if signature_matches and covers_transcript:
                    assembled_audio = cached_path
                    audio_offset = cached_offset
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
            "transcript": transcript
        }
    except Exception as e:
        await manager.send_error("filler_words", str(e))
        return {"error": str(e)}


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

    await manager.send_progress("filler_words", 0.3, "Assembling current timeline audio...")
    assembled_audio, assembled_fps, assembled_tl_start, _assembled_tl_end = assemble_current_timeline_audio()
    if not assembled_audio or assembled_fps is None or assembled_tl_start is None:
        return {"error": "Could not assemble current timeline audio"}

    await manager.send_progress("filler_words", 0.4, "Refining filler cut boundaries...")
    profile = audio_service.build_waveform_profile(assembled_audio)
    fillers_sorted = audio_service.merge_filler_detections(
        audio_service.refine_filler_segments_to_waveform(
            assembled_audio,
            selected,
            profile=profile,
        )
    )
    for filler in fillers_sorted:
        _set_timeline_detection_frames(filler, assembled_tl_start, assembled_fps)
    _debug(
        f"timeline duration={total_duration:.3f}s, first_selected={fillers_sorted[:settings.debug_log_limit]}"
    )

    sanitized_fillers = []
    skipped_invalid = 0
    raw_cut_frame_ranges: list[tuple[int, int]] = []
    for filler in fillers_sorted:
        raw_start = float(filler.get("start", 0.0))
        raw_end = float(filler.get("end", 0.0))
        filler_start = max(0.0, min(total_duration, raw_start))
        filler_end = max(0.0, min(total_duration, raw_end))
        if filler_end <= filler_start:
            skipped_invalid += 1
            continue

        start_frame = filler.get("start_frame")
        end_frame = filler.get("end_frame")
        if start_frame is None or end_frame is None:
            start_frame = tl_start + int(round(filler_start * fps))
            end_frame = tl_start + int(round(filler_end * fps))

        start_frame = max(int(tl_start), min(int(tl_end), int(start_frame)))
        end_frame = max(int(tl_start), min(int(tl_end), int(end_frame)))
        clamped_range = _clamp_filler_cut_frame_range(
            start_frame,
            end_frame,
            filler,
            int(tl_start),
            int(tl_end),
            fps,
        )
        if clamped_range is None:
            skipped_invalid += 1
            continue
        start_frame, end_frame = clamped_range
        if end_frame <= start_frame:
            skipped_invalid += 1
            continue

        raw_cut_frame_ranges.append((start_frame, end_frame))

        sanitized_fillers.append({
            "word": filler.get("word"),
            "start": round(filler_start, 3),
            "end": round(filler_end, 3),
            "start_frame": int(start_frame),
            "end_frame": int(end_frame),
            "duration": round(filler_end - filler_start, 3),
        })

    merged_raw_cut_ranges = _merge_frame_ranges(raw_cut_frame_ranges)
    cut_frame_ranges = []
    for start_frame, end_frame in merged_raw_cut_ranges:
        adjusted = _adjust_filler_cut_frame_range(start_frame, end_frame)
        if adjusted is None:
            continue
        padded_start, padded_end = adjusted
        cut_frame_ranges.append((
            max(int(tl_start), min(int(tl_end), padded_start)),
            max(int(tl_start), min(int(tl_end), padded_end)),
        ))

    cut_frame_ranges = _merge_frame_ranges(cut_frame_ranges)
    kept_frame_ranges = _subtract_frame_ranges(int(tl_start), int(tl_end), cut_frame_ranges)
    kept_segments = [
        _frame_range_to_segment(start_frame, end_frame, int(tl_start), fps)
        for start_frame, end_frame in kept_frame_ranges
    ]

    debug_payload["sanitized_selected"] = len(sanitized_fillers)
    debug_payload["skipped_invalid"] = skipped_invalid
    debug_payload["sanitized_sample"] = sanitized_fillers[:settings.debug_log_limit]
    debug_payload["kept_segment_count"] = len(kept_segments)
    debug_payload["kept_segments_sample"] = kept_segments[:settings.debug_log_limit]
    debug_payload["raw_cut_frame_range_count"] = len(merged_raw_cut_ranges)
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
