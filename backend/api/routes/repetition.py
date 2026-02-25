"""Repetition/take detection and removal endpoints."""

from fastapi import APIRouter
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from utils.audio_utils import extract_audio
from api.websocket import manager

router = APIRouter()


@router.post("/detect")
async def detect_repetitions():
    """Detect repeated takes in the timeline."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("repetition", 0.1, "Extracting audio...")
        audio_path = extract_audio(file_path)

        await manager.send_progress("repetition", 0.2, "Transcribing...")
        transcript = audio_service.transcribe(audio_path)

        await manager.send_progress("repetition", 0.6, "Detecting repetitions...")
        groups = await ai_service.detect_repetitions(transcript)

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

    backup = resolve_service.duplicate_timeline()

    return {
        "success": True,
        "removed_groups": len(groups),
        "backup_timeline": backup.GetName() if backup else None
    }
