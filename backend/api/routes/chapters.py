"""Chapter detection endpoints."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from services.caption_service import caption_service
from utils.audio_utils import extract_audio, extract_audio_segment, get_audio_duration
from utils.time_utils import seconds_to_frames
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.chapters")
router = APIRouter()


class ChapterItem(BaseModel):
    title: str = "Chapter"
    start_seconds: float = Field(ge=0)
    summary: str = ""
    selected: bool = True


class ChapterApplyRequest(BaseModel):
    chapters: list[ChapterItem]


class YouTubeChapterItem(BaseModel):
    title: str
    start_seconds: float = Field(ge=0)


class YouTubeExportRequest(BaseModel):
    chapters: list[YouTubeChapterItem]


@router.post("/detect")
@heavy_operation("Chapter Detection")
async def detect_chapters():
    """Detect natural chapter boundaries using AI."""
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("chapters", 0.02, "Extracting audio...")

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
        duration = get_audio_duration(audio_path)

        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.50
            await manager.send_progress("chapters", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, progress_fn=progress_fn,
        )

        await manager.send_progress("chapters", 0.6, "AI analyzing chapters...")
        raw_chapters = await ai_service.detect_chapters(transcript["text"])

        # Convert percentage positions to actual timestamps
        chapters = []
        for ch in raw_chapters:
            pct = ch.get("approximate_position", 0) / 100.0
            start_seconds = clip_context["clip_timeline_offset"] + (pct * duration)
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

    selected = [c for c in request.chapters if c.selected]
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
        frame_id = seconds_to_frames(ch.start_seconds, fps) + start_frame
        success = timeline.AddMarker(
            frame_id, "Blue",
            ch.title,
            ch.summary,
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
async def export_youtube_chapters(request: YouTubeExportRequest):
    """Export chapters as YouTube-formatted timestamps."""
    text = caption_service.generate_youtube_chapters(
        [ch.model_dump() for ch in request.chapters]
    )
    return {"text": text}
