"""Dynamic zoom cuts endpoints."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.video_service import video_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.time_utils import seconds_to_frames
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.zoom")

router = APIRouter()


class ZoomConfig(BaseModel):
    zoom_amount: float = 1.15
    zoom_duration_seconds: float = 2.0
    min_gap_seconds: float = 3.0
    max_zooms: int = 50


class ZoomApplyRequest(BaseModel):
    zoom_points: list[dict]


@router.post("/analyze")
@heavy_operation("Zoom Analysis")
async def analyze_zoom_points(config: ZoomConfig):
    """Analyze timeline for optimal zoom cut points."""
    clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No video clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("zoom", 0.02, "Extracting audio...")

        clip_context = resolve_service.get_clip_audio_context(clip)
        if clip_context is None:
            return {"error": "Could not determine clip timing context"}

        full_audio = extract_audio(file_path)
        audio_path = full_audio
        if clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
            audio_path = extract_audio_segment(
                full_audio,
                clip_context["source_start_seconds"],
                clip_context["source_end_seconds"],
            )

        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.50
            await manager.send_progress("zoom", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, progress_fn=progress_fn,
        )

        await manager.send_progress("zoom", 0.6, "Analyzing audio energy...")
        energy_data = audio_service.get_audio_energy(audio_path)

        await manager.send_progress("zoom", 0.8, "Calculating zoom points...")
        zoom_points = video_service.calculate_zoom_points(
            transcript,
            energy_data,
            min_gap_seconds=config.min_gap_seconds,
            zoom_amount=config.zoom_amount,
            zoom_duration_seconds=config.zoom_duration_seconds,
            max_zooms=config.max_zooms
        )
        for zp in zoom_points:
            zp["time"] += clip_context["clip_timeline_offset"]

        await manager.send_progress("zoom", 1.0, f"Found {len(zoom_points)} zoom points")

        return {
            "zoom_points": zoom_points,
            "count": len(zoom_points)
        }
    except Exception as e:
        await manager.send_error("zoom", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_zoom_cuts(request: ZoomApplyRequest):
    """Apply zoom effects to the timeline clips."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    selected = [z for z in request.zoom_points if z.get("selected", True)]
    if not selected:
        return {"error": "No zoom points selected"}

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
    start_frame = timeline.GetStartFrame()

    try:
        clips = resolve_service.get_timeline_clips("video", 1)
        applied = 0

        for zoom in selected:
            zoom_frame = seconds_to_frames(zoom["time"], fps) + start_frame

            # Find the clip at this frame position
            for clip in clips:
                clip_start = clip.GetStart()
                clip_end = clip.GetEnd()
                if clip_start <= zoom_frame < clip_end:
                    # Apply zoom properties
                    zoom_val = zoom.get("zoom", 1.15)
                    clip.SetProperty("ZoomX", zoom_val)
                    clip.SetProperty("ZoomY", zoom_val)
                    if zoom.get("pan_x", 0) != 0:
                        clip.SetProperty("Pan", zoom["pan_x"])
                    if zoom.get("pan_y", 0) != 0:
                        clip.SetProperty("Tilt", zoom["pan_y"])
                    applied += 1
                    break

        await manager.send_complete("zoom_apply", {"applied_count": applied})

        from api.routes.undo import _record
        _record("Dynamic Zoom", backup_name,
                f"Applied {applied} zoom effects")

        return {
            "success": True,
            "applied_count": applied,
            "backup_timeline": backup_name,
            "message": f"Applied {applied} zoom effects. Backup: {backup_name}"
        }
    except Exception as e:
        await manager.send_error("zoom_apply", str(e))
        return {"error": str(e)}
