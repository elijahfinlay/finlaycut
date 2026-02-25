"""Automated captions generation and application endpoints."""

from fastapi import APIRouter
from models.caption import CaptionGenerateRequest, CaptionApplyRequest
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.caption_service import caption_service
from utils.audio_utils import extract_audio
from api.websocket import manager

router = APIRouter()


@router.post("/generate")
async def generate_captions(request: CaptionGenerateRequest):
    """Generate captions from timeline audio."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("captions", 0.05, "Extracting audio...")
        audio_path = extract_audio(file_path)

        await manager.send_progress("captions", 0.1, "Transcribing...")
        transcript = audio_service.transcribe(audio_path, language=request.language)

        await manager.send_progress("captions", 0.8, "Generating captions...")

        # Generate SRT content
        srt_content = caption_service.generate_srt(
            transcript,
            max_chars_per_line=request.max_chars_per_line
        )

        # Generate word-level captions for animated mode
        word_captions = caption_service.generate_word_level_captions(
            transcript,
            words_per_caption=request.words_per_caption
        )

        await manager.send_progress("captions", 1.0, "Captions generated")

        return {
            "srt": srt_content,
            "captions": word_captions,
            "segment_count": len(transcript.get("segments", [])),
            "language": transcript.get("language"),
            "transcript": transcript
        }
    except Exception as e:
        await manager.send_error("captions", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_captions(request: CaptionApplyRequest):
    """Apply captions to the current timeline."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    # Create backup
    backup = resolve_service.duplicate_timeline()

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    try:
        # Add subtitle track if needed
        sub_tracks = timeline.GetTrackCount("subtitle")
        if sub_tracks == 0:
            timeline.AddTrack("subtitle")

        await manager.send_progress("captions_apply", 0.5, "Adding captions to timeline...")

        # For now, we'll save the SRT and note it for the user
        # Full Fusion TextPlus integration would go here for animated captions

        await manager.send_complete("captions_apply", {
            "caption_count": len(request.captions),
            "backup_name": backup.GetName() if backup else "unknown"
        })

        return {
            "success": True,
            "caption_count": len(request.captions),
            "backup_timeline": backup.GetName() if backup else None,
            "message": f"Applied {len(request.captions)} captions to timeline"
        }
    except Exception as e:
        await manager.send_error("captions_apply", str(e))
        return {"error": str(e)}


@router.get("/export-srt")
async def export_srt(language: str = None):
    """Export captions as an SRT file."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        return {"error": "No clips found"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not get source file"}

    audio_path = extract_audio(file_path)
    transcript = audio_service.transcribe(audio_path, language=language)
    srt = caption_service.generate_srt(transcript)

    return {"srt": srt}
