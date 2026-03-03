"""Viral clip detection and creation endpoints."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from services.video_service import video_service
from utils.audio_utils import extract_audio, extract_audio_segment, get_audio_duration
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.viral")
router = APIRouter()


class ViralClipCreate(BaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    suggested_title: str = "Viral Clip"


@router.post("/detect")
@heavy_operation("Viral Clip Detection")
async def detect_viral_clips():
    """Score transcript segments for viral potential."""
    clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No video clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("viral", 0.02, "Extracting audio...")

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
        duration = get_audio_duration(audio_path)

        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.40
            await manager.send_progress("viral", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, progress_fn=progress_fn,
        )

        await manager.send_progress("viral", 0.5, "AI scoring clips...")
        raw_clips = await ai_service.score_viral_potential(transcript["text"], duration)

        # Convert percentage positions to timestamps
        viral_clips = []
        for clip in raw_clips:
            start_pct = clip.get("start_pct", 0) / 100.0
            end_pct = clip.get("end_pct", 10) / 100.0
            viral_clips.append({
                "start_seconds": clip_timeline_offset + (start_pct * duration),
                "end_seconds": clip_timeline_offset + (end_pct * duration),
                "duration_seconds": (end_pct - start_pct) * duration,
                "score": clip.get("score", 5),
                "reason": clip.get("reason", ""),
                "suggested_title": clip.get("suggested_title", ""),
                "selected": True
            })

        # Sort by score descending
        viral_clips.sort(key=lambda c: c["score"], reverse=True)

        await manager.send_progress("viral", 1.0, f"Found {len(viral_clips)} potential clips")

        return {
            "clips": viral_clips,
            "count": len(viral_clips)
        }
    except Exception as e:
        await manager.send_error("viral", str(e))
        return {"error": str(e)}


@router.post("/create")
async def create_viral_clip(clip: ViralClipCreate):
    """Create a new 9:16 timeline from a viral clip segment."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    try:
        start_seconds = clip.start_seconds
        end_seconds = clip.end_seconds
        title = clip.suggested_title

        # Get current resolution for vertical crop calculation
        width = int(timeline.GetSetting("timelineResolutionWidth") or 1920)
        height = int(timeline.GetSetting("timelineResolutionHeight") or 1080)

        crop = video_service.calculate_vertical_crop(width, height)

        # Build a new timeline with just this clip segment
        kept = [{"start": start_seconds, "end": end_seconds}]
        safe_title = title.replace("/", "-").replace("\\", "-")[:50]
        result = resolve_service.rebuild_timeline_keeping(
            kept, new_name=f"Short - {safe_title}"
        )

        if not result["success"]:
            return {"error": result.get("error", "Failed to create clip timeline")}

        # Try to set the new timeline to 1080x1920 (9:16)
        new_tl = resolve_service.get_current_timeline()
        if new_tl:
            new_tl.SetSetting("timelineResolutionWidth", "1080")
            new_tl.SetSetting("timelineResolutionHeight", "1920")

        return {
            "success": True,
            "crop_params": crop,
            "new_timeline": result.get("timeline_name"),
            "message": f"Created 9:16 timeline: {result.get('timeline_name')}"
        }
    except Exception as e:
        return {"error": str(e)}
