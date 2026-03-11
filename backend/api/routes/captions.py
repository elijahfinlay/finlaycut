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


async def apply_srt_to_timeline(
    *,
    operation_name: str,
    srt_content: str,
    entry_count: int,
    undo_label: str,
    undo_description: str,
    srt_filename: str = "captions.srt",
    ensure_new_track: bool = False,
) -> dict:
    """Save SRT content to disk and import it into the current timeline."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    await manager.send_progress(operation_name, 0.1, "Creating backup...")

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
        sub_tracks = int(timeline.GetTrackCount("subtitle") or 0)
        if sub_tracks == 0 or ensure_new_track:
            timeline.AddTrack("subtitle")

        await manager.send_progress(operation_name, 0.3, "Saving SRT file...")

        import os

        srt_path = os.path.join(str(settings.temp_dir), srt_filename)
        with open(srt_path, "w", encoding="utf-8") as handle:
            handle.write(srt_content or "")

        await manager.send_progress(operation_name, 0.6, "Importing subtitles...")

        if os.path.exists(srt_path):
            timeline.ImportIntoTimeline(
                srt_path,
                {"autoImportSourceClipsIntoMediaPool": True},
            )

        await manager.send_progress(operation_name, 1.0, "Done")
        await manager.send_complete(
            operation_name,
            {"caption_count": entry_count, "backup_name": backup_name},
        )

        from api.routes.undo import _record

        _record(undo_label, backup_name, undo_description)

        return {
            "success": True,
            "caption_count": entry_count,
            "srt_path": srt_path,
            "backup_timeline": backup_name,
            "message": f"Applied {entry_count} subtitle entries. SRT saved to {srt_path}. Backup: {backup_name}",
        }
    except Exception as e:
        await manager.send_error(operation_name, str(e))
        return {"error": str(e)}


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
            scaled = 0.05 + pct * 0.70
            await manager.send_progress("captions", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, language=request.language, progress_fn=progress_fn,
        )

        for seg in transcript.get("segments", []):
            seg["start"] += clip_context["clip_timeline_offset"]
            seg["end"] += clip_context["clip_timeline_offset"]
        for word in transcript.get("words", []):
            local_start = float(word.get("start", 0.0))
            local_end = float(word.get("end", local_start))
            word["start"] = local_start + clip_context["clip_timeline_offset"]
            word["end"] = local_end + clip_context["clip_timeline_offset"]
            word["start_frame"] = clip_context["clip_start_frame"] + int(round(local_start * clip_context["fps"]))
            word["end_frame"] = clip_context["clip_start_frame"] + int(round(local_end * clip_context["fps"]))
            if clip_context.get("source_start_frame") is not None:
                word["source_start_frame"] = int(clip_context["source_start_frame"]) + int(round(local_start * clip_context["fps"]))
                word["source_end_frame"] = int(clip_context["source_start_frame"]) + int(round(local_end * clip_context["fps"]))

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
    return await apply_srt_to_timeline(
        operation_name="captions_apply",
        srt_content=request.srt or "",
        entry_count=len(request.captions),
        undo_label="Captions",
        undo_description=f"Applied {len(request.captions)} captions",
        srt_filename="captions.srt",
        ensure_new_track=False,
    )


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

    clip_context = resolve_service.get_clip_audio_context(clip)
    audio_path = extract_audio(file_path)
    if clip_context and clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
        audio_path = extract_audio_segment(
            audio_path,
            clip_context["source_start_seconds"],
            clip_context["source_end_seconds"],
        )
    transcript = audio_service.transcribe(audio_path, language=language)
    srt = caption_service.generate_srt(transcript)

    return {"srt": srt}
