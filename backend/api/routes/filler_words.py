"""Filler word detection and removal endpoints."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.filler_words")

router = APIRouter()


def _debug_enabled() -> bool:
    return bool(getattr(settings, "debug_cut_planning", False))


def _debug(message: str):
    if _debug_enabled():
        logger.debug(message)


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
        await manager.send_progress("filler_words", 0.5, "Detecting filler words...")
        detections = audio_service.detect_filler_words(request.transcript, request.filler_words)
        await manager.send_progress("filler_words", 1.0, f"Found {len(detections)} filler words")
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

        timeline = resolve_service.get_current_timeline()
        fps_str = timeline.GetSetting("timelineFrameRate") if timeline else None
        fps = float(fps_str) if fps_str else 24.0
        timeline_start_frame = timeline.GetStartFrame() if timeline else 0
        clip_timeline_offset = (clip.GetStart() - timeline_start_frame) / fps
        _debug(
            f"analyze clip context: clip_start={clip.GetStart()}, clip_end={clip.GetEnd()}, "
            f"timeline_start={timeline_start_frame}, offset_s={clip_timeline_offset:.3f}"
        )

        full_audio = extract_audio(file_path)

        # Try to extract just the timeline-visible portion
        try:
            left_offset = clip.GetLeftOffset()
            clip_duration_frames = clip.GetEnd() - clip.GetStart()
            source_start = left_offset / fps
            source_end = (left_offset + clip_duration_frames) / fps
            logger.info("left_offset=%s, clip_frames=%s, source_start=%.2fs, source_end=%.2fs",
                        left_offset, clip_duration_frames, source_start, source_end)
            audio_path = extract_audio_segment(full_audio, source_start, source_end)
        except Exception as e:
            logger.warning("Segment extraction failed (%s), using full audio", e)
            audio_path = full_audio

        # Use async transcribe with real progress (0.05 - 0.85 range)
        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.80
            await manager.send_progress("filler_words", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path,
            language=request.language,
            progress_fn=progress_fn,
        )

        await manager.send_progress("filler_words", 0.88, "Detecting filler words...")
        detections = audio_service.detect_filler_words(transcript, request.filler_words)
        for d in detections:
            d["start"] += clip_timeline_offset
            d["end"] += clip_timeline_offset
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

    fillers_sorted = sorted(selected, key=lambda d: d["start"])
    _debug(
        f"timeline duration={total_duration:.3f}s, first_selected={fillers_sorted[:settings.debug_log_limit]}"
    )

    kept_segments = []
    cursor = 0.0
    sanitized_fillers = []
    skipped_invalid = 0
    for filler in fillers_sorted:
        raw_start = float(filler.get("start", 0.0))
        raw_end = float(filler.get("end", 0.0))
        filler_start = max(0.0, min(total_duration, raw_start))
        filler_end = max(0.0, min(total_duration, raw_end))
        if filler_end <= filler_start:
            skipped_invalid += 1
            continue

        sanitized_fillers.append({
            "word": filler.get("word"),
            "start": round(filler_start, 3),
            "end": round(filler_end, 3),
            "duration": round(filler_end - filler_start, 3),
        })

        if filler_start > cursor:
            kept_segments.append({"start": cursor, "end": filler_start})
        cursor = max(cursor, filler_end)

    if cursor < total_duration:
        kept_segments.append({"start": cursor, "end": total_duration})

    debug_payload["sanitized_selected"] = len(sanitized_fillers)
    debug_payload["skipped_invalid"] = skipped_invalid
    debug_payload["sanitized_sample"] = sanitized_fillers[:settings.debug_log_limit]
    debug_payload["kept_segment_count"] = len(kept_segments)
    debug_payload["kept_segments_sample"] = kept_segments[:settings.debug_log_limit]
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

    total_removed = sum(d["end"] - d["start"] for d in sanitized_fillers)

    await manager.send_progress("filler_words", 1.0, "Done")
    await manager.send_complete("filler_words", {
        "removed_count": len(sanitized_fillers),
        "total_removed": round(total_removed, 1)
    })

    from api.routes.undo import _record
    _record("Filler Word Removal", backup_name,
            f"Removed {len(sanitized_fillers)} filler words ({total_removed:.1f}s)")

    response = {
        "success": True,
        "removed_count": len(sanitized_fillers),
        "total_removed_seconds": round(total_removed, 1),
        "backup_timeline": backup_name,
        "timeline_name": result.get("timeline_name"),
        "message": f"Removed {len(sanitized_fillers)} filler words ({total_removed:.1f}s) on the current timeline. Backup: {backup_name}"
    }
    if _debug_enabled():
        debug_payload["rebuild_debug"] = result.get("debug")
        response["debug"] = debug_payload
    _debug(
        f"apply success: removed={len(sanitized_fillers)}, total_removed={total_removed:.3f}s, "
        f"new_timeline={result.get('timeline_name')}"
    )
    return response
