"""Automated captions generation and application endpoints."""

import logging
from fastapi import APIRouter
from models.caption import CaptionGenerateRequest, CaptionApplyRequest
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.caption_service import caption_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.captions")

router = APIRouter()


@router.post("/generate")
@heavy_operation("Caption Generation")
async def generate_captions(request: CaptionGenerateRequest):
    """Generate captions from timeline audio."""
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("captions", 0.02, "Extracting audio...")

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
            scaled = 0.05 + pct * 0.70
            await manager.send_progress("captions", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, language=request.language, progress_fn=progress_fn,
        )

        for seg in transcript.get("segments", []):
            seg["start"] += clip_timeline_offset
            seg["end"] += clip_timeline_offset
        for word in transcript.get("words", []):
            word["start"] += clip_timeline_offset
            word["end"] += clip_timeline_offset

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

    await manager.send_progress("captions_apply", 0.1, "Creating backup...")

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

    try:
        # Add subtitle track if needed
        sub_tracks = timeline.GetTrackCount("subtitle")
        if sub_tracks == 0:
            timeline.AddTrack("subtitle")

        await manager.send_progress("captions_apply", 0.3, "Saving SRT file...")

        # Save SRT to disk so the user can import it
        import os
        srt_path = os.path.join(str(settings.temp_dir), "captions.srt")
        if request.srt:
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(request.srt)

        await manager.send_progress("captions_apply", 0.6, "Importing subtitles...")

        # Try to import the SRT into the timeline
        # Resolve's ImportIntoTimeline can handle SRT files
        if os.path.exists(srt_path):
            timeline.ImportIntoTimeline(srt_path, {
                "autoImportSourceClipsIntoMediaPool": True
            })

        await manager.send_progress("captions_apply", 1.0, "Done")

        await manager.send_complete("captions_apply", {
            "caption_count": len(request.captions),
            "backup_name": backup_name
        })

        from api.routes.undo import _record
        _record("Captions", backup_name,
                f"Applied {len(request.captions)} captions")

        return {
            "success": True,
            "caption_count": len(request.captions),
            "srt_path": srt_path,
            "backup_timeline": backup_name,
            "message": f"Applied {len(request.captions)} captions. SRT saved to {srt_path}. Backup: {backup_name}"
        }
    except Exception as e:
        await manager.send_error("captions_apply", str(e))
        return {"error": str(e)}


@router.get("/export-srt")
async def export_srt(language: str = None):
    """Export captions as an SRT file."""
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not get source file"}

    audio_path = extract_audio(file_path)
    transcript = audio_service.transcribe(audio_path, language=language)
    srt = caption_service.generate_srt(transcript)

    return {"srt": srt}
