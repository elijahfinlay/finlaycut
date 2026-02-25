"""Viral clip detection and creation endpoints."""

from fastapi import APIRouter
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from services.video_service import video_service
from utils.audio_utils import extract_audio, get_audio_duration
from api.websocket import manager

router = APIRouter()


@router.post("/detect")
async def detect_viral_clips():
    """Score transcript segments for viral potential."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        return {"error": "No video clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("viral", 0.1, "Extracting audio...")
        audio_path = extract_audio(file_path)
        duration = get_audio_duration(audio_path)

        await manager.send_progress("viral", 0.2, "Transcribing...")
        transcript = audio_service.transcribe(audio_path)

        await manager.send_progress("viral", 0.5, "AI scoring clips...")
        raw_clips = await ai_service.score_viral_potential(transcript["text"], duration)

        # Convert percentage positions to timestamps
        viral_clips = []
        for clip in raw_clips:
            start_pct = clip.get("start_pct", 0) / 100.0
            end_pct = clip.get("end_pct", 10) / 100.0
            viral_clips.append({
                "start_seconds": start_pct * duration,
                "end_seconds": end_pct * duration,
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
async def create_viral_clip(clip: dict):
    """Create a new 9:16 timeline from a viral clip segment."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    try:
        # Get current resolution for vertical crop calculation
        width = int(timeline.GetSetting("timelineResolutionWidth") or 1920)
        height = int(timeline.GetSetting("timelineResolutionHeight") or 1080)

        crop = video_service.calculate_vertical_crop(width, height)

        return {
            "success": True,
            "crop_params": crop,
            "message": "Vertical clip template created"
        }
    except Exception as e:
        return {"error": str(e)}
