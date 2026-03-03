"""Silence removal endpoints."""

import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks
from models.silence import SilenceConfig, SilenceApplyRequest
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.silence")

router = APIRouter()


@router.post("/analyze")
@heavy_operation("Silence Analysis")
async def analyze_silence(config: SilenceConfig):
    """
    Analyze the current timeline for silent segments.
    Extracts audio, runs silence detection, returns segments.
    """
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found in timeline. Make sure your timeline has at least one clip."}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path for the clip."}

    await manager.send_progress("silence", 0.1, "Extracting audio...")

    try:
        # Extract only the timeline-visible portion of audio so timestamps
        # are relative to timeline start (not source-file start)
        timeline = resolve_service.get_current_timeline()
        fps_str = timeline.GetSetting("timelineFrameRate") if timeline else None
        fps = float(fps_str) if fps_str else 24.0
        timeline_start_frame = timeline.GetStartFrame() if timeline else 0
        clip_timeline_offset = (clip.GetStart() - timeline_start_frame) / fps

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

        # Detect silences with real progress updates
        async def progress_cb(pct, msg):
            scaled = 0.15 + pct * 0.75
            await manager.send_progress("silence", scaled, msg)

        silences = await audio_service.detect_silence_with_progress(
            audio_path,
            threshold_db=config.threshold_db,
            min_duration_ms=config.min_duration_ms,
            progress_fn=progress_cb,
        )

        for s in silences:
            s["start"] += clip_timeline_offset
            s["end"] += clip_timeline_offset

        total_silence = sum(s["duration"] for s in silences)

        await manager.send_progress("silence", 1.0, f"Found {len(silences)} silent segments")

        return {
            "segments": silences,
            "total_silence_seconds": total_silence,
            "segment_count": len(silences),
            "audio_path": audio_path
        }
    except Exception as e:
        await manager.send_error("silence", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_silence_removal(request: SilenceApplyRequest):
    """
    Apply silence removal to the current timeline.
    Creates a backup first, then edits the active timeline in place without the
    selected silent segments.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    selected_silences = [s for s in request.segments if s.selected]
    if not selected_silences:
        return {"error": "No silent segments selected for removal"}

    await manager.send_progress("silence_apply", 0.1, "Creating backup...")

    # Create backup timeline (non-blocking — proceed even if backup fails)
    backup = resolve_service.duplicate_timeline()
    backup_name = "none"
    if backup is not None:
        try:
            backup_name = backup.GetName()
        except Exception:
            backup_name = "backup"
    else:
        logger.warning("Could not create backup timeline")

    await manager.send_progress("silence_apply", 0.3, "Calculating kept segments...")

    try:
        timeline = resolve_service.get_current_timeline()
        if timeline is None:
            return {"error": "No active timeline"}

        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0

        # Get total timeline duration in seconds
        tl_start = timeline.GetStartFrame()
        tl_end = timeline.GetEndFrame()
        total_duration = (tl_end - tl_start) / fps

        # Sort silences by start time
        silences_sorted = sorted(selected_silences, key=lambda s: s.start)
        padding_s = request.padding_ms / 1000.0

        # Build the KEPT segments (everything that is NOT a selected silence)
        kept_segments = []
        cursor = 0.0
        for silence in silences_sorted:
            # Apply padding: shrink the silence by padding on each side
            cut_start = silence.start + padding_s
            cut_end = silence.start + silence.duration - padding_s

            if cut_start < cut_end and cut_start > cursor:
                # Keep the part before this silence
                if cursor < cut_start:
                    kept_segments.append({"start": cursor, "end": cut_start})
                cursor = cut_end
            else:
                # Silence too short after padding — skip it
                continue

        # Keep the tail after the last silence
        if cursor < total_duration:
            kept_segments.append({"start": cursor, "end": total_duration})

        if not kept_segments:
            return {"error": "Nothing left to keep after removing all silences"}

        await manager.send_progress("silence_apply", 0.5, "Applying edits to current timeline...")

        result = resolve_service.rebuild_timeline_keeping(kept_segments, in_place=True)

        if not result["success"]:
            return {"error": result.get("error", "Failed to apply edits to current timeline")}

        total_removed = sum(s.duration for s in selected_silences)

        await manager.send_progress("silence_apply", 1.0, "Done")
        await manager.send_complete("silence_apply", {
            "removed_count": len(selected_silences),
            "total_removed": round(total_removed, 1),
            "backup_name": backup_name
        })

        # Record in undo history
        from api.routes.undo import _record
        _record("Silence Removal", backup_name,
                f"Removed {len(selected_silences)} silences ({total_removed:.1f}s)")

        return {
            "success": True,
            "removed_count": len(selected_silences),
            "total_removed_seconds": round(total_removed, 1),
            "backup_timeline": backup_name,
            "timeline_name": result.get("timeline_name"),
            "message": f"Removed {len(selected_silences)} silences ({total_removed:.1f}s) on the current timeline. Backup: {backup_name}"
        }
    except Exception as e:
        await manager.send_error("silence_apply", str(e))
        return {"error": str(e)}
