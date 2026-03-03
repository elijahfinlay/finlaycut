"""Repetition/take detection and removal endpoints."""

import logging
from fastapi import APIRouter
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.repetition")

router = APIRouter()


@router.post("/detect")
@heavy_operation("Repetition Detection")
async def detect_repetitions():
    """Detect repeated takes in the timeline."""
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("repetition", 0.02, "Extracting audio...")

        timeline = resolve_service.get_current_timeline()
        fps_str = timeline.GetSetting("timelineFrameRate") if timeline else None
        fps = float(fps_str) if fps_str else 24.0
        timeline_start_frame = timeline.GetStartFrame() if timeline else 0
        clip_timeline_offset = (clip.GetStart() - timeline_start_frame) / fps
        left_offset = clip.GetLeftOffset()
        clip_duration_frames = clip.GetEnd() - clip.GetStart()
        source_start = left_offset / fps
        source_end = (left_offset + clip_duration_frames) / fps

        full_audio = extract_audio(file_path)
        audio_path = extract_audio_segment(full_audio, source_start, source_end)

        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.50
            await manager.send_progress("repetition", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, progress_fn=progress_fn,
        )

        await manager.send_progress("repetition", 0.6, "Detecting repetitions...")
        groups = await ai_service.detect_repetitions(transcript)

        for group in groups:
            takes = group.get("takes", [])
            for take in takes:
                if "start" in take:
                    take["start"] += clip_timeline_offset
                if "end" in take:
                    take["end"] += clip_timeline_offset

        await manager.send_progress("repetition", 1.0, f"Found {len(groups)} repeated sections")

        return {
            "groups": groups,
            "count": len(groups)
        }
    except Exception as e:
        await manager.send_error("repetition", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_repetition_removal(groups: list[dict]):
    """Remove unwanted takes, keeping the best one from each group."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not groups:
        return {"error": "No repetition groups provided"}

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

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    tl_start = timeline.GetStartFrame()
    tl_end = timeline.GetEndFrame()
    total_duration = (tl_end - tl_start) / fps

    # For each group, identify the segments to REMOVE (all takes except the best)
    segments_to_remove = []
    for group in groups:
        best_idx = group.get("best_index", 0)
        takes = group.get("takes", [])
        for ti, take in enumerate(takes):
            if ti != best_idx:
                segments_to_remove.append({
                    "start": take.get("start", 0),
                    "end": take.get("end", 0),
                })

    if not segments_to_remove:
        return {"success": True, "message": "Nothing to remove"}

    # Sort removals and build kept segments
    segments_to_remove.sort(key=lambda s: s["start"])
    kept_segments = []
    cursor = 0.0
    for rem in segments_to_remove:
        if rem["start"] > cursor:
            kept_segments.append({"start": cursor, "end": rem["start"]})
        cursor = max(cursor, rem["end"])
    if cursor < total_duration:
        kept_segments.append({"start": cursor, "end": total_duration})

    result = resolve_service.rebuild_timeline_keeping(kept_segments, in_place=True)
    if not result["success"]:
        return {"error": result.get("error", "Failed to apply edits to current timeline")}

    total_removed = sum(r["end"] - r["start"] for r in segments_to_remove)

    from api.routes.undo import _record
    _record("Repetition Removal", backup_name,
            f"Removed {len(segments_to_remove)} takes ({total_removed:.1f}s)")

    return {
        "success": True,
        "removed_takes": len(segments_to_remove),
        "total_removed_seconds": round(total_removed, 1),
        "backup_timeline": backup_name,
        "timeline_name": result.get("timeline_name"),
        "message": f"Removed {len(segments_to_remove)} duplicate takes ({total_removed:.1f}s) on the current timeline. Backup: {backup_name}"
    }
