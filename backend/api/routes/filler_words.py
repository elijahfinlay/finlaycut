"""Filler word detection and removal endpoints."""

from fastapi import APIRouter
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio
from utils.time_utils import seconds_to_frames
from api.websocket import manager

router = APIRouter()


@router.post("/analyze")
async def analyze_filler_words(
    language: Optional[str] = None,
    filler_words: Optional[list[str]] = None
):
    """Transcribe the timeline audio and detect filler words."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("filler_words", 0.05, "Extracting audio...")
        audio_path = extract_audio(file_path)

        await manager.send_progress("filler_words", 0.1, "Transcribing...")

        async def progress_cb(pct, msg):
            await manager.send_progress("filler_words", 0.1 + pct * 0.7, msg)

        transcript = audio_service.transcribe(audio_path, language=language)

        await manager.send_progress("filler_words", 0.85, "Detecting filler words...")
        detections = audio_service.detect_filler_words(transcript, filler_words)

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
    """Apply filler word removal by marking them on the timeline."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    selected = [d for d in detections if d.get("selected", True)]
    if not selected:
        return {"error": "No filler words selected for removal"}

    # Create backup
    backup = resolve_service.duplicate_timeline()

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    start_frame = timeline.GetStartFrame()

    # Mark filler words on timeline
    for i, detection in enumerate(selected):
        frame_id = seconds_to_frames(detection["start"], fps) + start_frame
        duration_frames = max(1, seconds_to_frames(
            detection["end"] - detection["start"], fps
        ))
        timeline.AddMarker(
            frame_id, "Yellow",
            f"Filler: {detection['word']}",
            detection.get("context", ""),
            duration_frames
        )

    return {
        "success": True,
        "removed_count": len(selected),
        "backup_timeline": backup.GetName() if backup else None,
        "message": f"Marked {len(selected)} filler words on timeline"
    }
