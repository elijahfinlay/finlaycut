"""Chapter detection endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from services.caption_service import caption_service
from utils.audio_utils import extract_audio, get_audio_duration
from utils.time_utils import seconds_to_frames
from api.websocket import manager

router = APIRouter()


class ChapterApplyRequest(BaseModel):
    chapters: list[dict]


@router.post("/detect")
async def detect_chapters():
    """Detect natural chapter boundaries using AI."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("chapters", 0.1, "Extracting audio...")
        audio_path = extract_audio(file_path)
        duration = get_audio_duration(audio_path)

        await manager.send_progress("chapters", 0.2, "Transcribing...")
        transcript = audio_service.transcribe(audio_path)

        await manager.send_progress("chapters", 0.6, "AI analyzing chapters...")
        raw_chapters = await ai_service.detect_chapters(transcript["text"])

        # Convert percentage positions to actual timestamps
        chapters = []
        for ch in raw_chapters:
            pct = ch.get("approximate_position", 0) / 100.0
            start_seconds = pct * duration
            chapters.append({
                "title": ch.get("title", "Chapter"),
                "start_seconds": start_seconds,
                "summary": ch.get("summary", ""),
                "selected": True
            })

        await manager.send_progress("chapters", 1.0, f"Found {len(chapters)} chapters")

        return {
            "chapters": chapters,
            "count": len(chapters),
            "duration": duration
        }
    except Exception as e:
        await manager.send_error("chapters", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_chapters(request: ChapterApplyRequest):
    """Apply chapter markers to the timeline."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    selected = [c for c in request.chapters if c.get("selected", True)]
    if not selected:
        return {"error": "No chapters selected"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    start_frame = timeline.GetStartFrame()

    applied = 0
    for ch in selected:
        frame_id = seconds_to_frames(ch["start_seconds"], fps) + start_frame
        success = timeline.AddMarker(
            frame_id, "Blue",
            ch.get("title", "Chapter"),
            ch.get("summary", ""),
            1
        )
        if success:
            applied += 1

    return {
        "success": True,
        "applied_count": applied,
        "message": f"Added {applied} chapter markers"
    }


@router.post("/export-youtube")
async def export_youtube_chapters(chapters: list[dict]):
    """Export chapters as YouTube-formatted timestamps."""
    text = caption_service.generate_youtube_chapters(chapters)
    return {"text": text}
