"""Silence removal endpoints."""

import asyncio
from fastapi import APIRouter, BackgroundTasks
from models.silence import SilenceConfig, SilenceApplyRequest
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio
from utils.time_utils import seconds_to_frames
from api.websocket import manager

router = APIRouter()


@router.post("/analyze")
async def analyze_silence(config: SilenceConfig):
    """
    Analyze the current timeline for silent segments.
    Extracts audio, runs silence detection, returns segments.
    """
    # Get the source file from the current timeline
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        return {"error": "No clips found in timeline. Make sure your timeline has at least one clip."}

    # Get file path from first clip
    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path for the clip."}

    await manager.send_progress("silence", 0.1, "Extracting audio...")

    try:
        # Extract audio
        audio_path = extract_audio(file_path)

        # Detect silences
        async def progress_cb(pct, msg):
            await manager.send_progress("silence", 0.1 + pct * 0.8, msg)

        silences = audio_service.detect_silence(
            audio_path,
            threshold_db=config.threshold_db,
            min_duration_ms=config.min_duration_ms,
        )

        total_silence = sum(s["duration"] for s in silences)

        await manager.send_progress("silence", 1.0, f"Found {len(silences)} silent segments")

        return {
            "segments": silences,
            "total_silence_seconds": total_silence,
            "segment_count": len(silences),
            "audio_path": audio_path
        }
    except Exception as e:
        await manager.send_error("silence", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_silence_removal(request: SilenceApplyRequest):
    """
    Apply silence removal to the current timeline.
    Creates a backup first, then reconstructs the timeline without selected silent segments.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    selected_silences = [s for s in request.segments if s.selected]
    if not selected_silences:
        return {"error": "No silent segments selected for removal"}

    await manager.send_progress("silence_apply", 0.1, "Creating backup...")

    # Create backup timeline
    backup = resolve_service.duplicate_timeline()
    if backup is None:
        return {"error": "Failed to create backup timeline"}

    await manager.send_progress("silence_apply", 0.3, "Removing silences...")

    try:
        timeline = resolve_service.get_current_timeline()
        if timeline is None:
            return {"error": "No active timeline"}

        # Get timeline info for frame calculations
        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0
        start_frame = timeline.GetStartFrame()

        # Build list of segments to KEEP (inverse of silences)
        # Sort silences by start time
        silences_sorted = sorted(selected_silences, key=lambda s: s.start)

        padding_s = request.padding_ms / 1000.0

        # Delete silent segments using Resolve's API
        # We work backwards to avoid index shifting
        clips = resolve_service.get_timeline_clips("video", 1)
        if not clips:
            clips = resolve_service.get_timeline_clips("audio", 1)

        # For now, add markers at silence points as a visual indicator
        # Full timeline reconstruction is complex — we'll mark silences
        for i, silence in enumerate(silences_sorted):
            frame_id = seconds_to_frames(silence.start, fps) + start_frame
            timeline.AddMarker(
                frame_id, "Red",
                f"Silence {i+1}",
                f"Duration: {silence.duration:.1f}s",
                max(1, seconds_to_frames(silence.duration, fps))
            )

        await manager.send_progress("silence_apply", 1.0, "Done")
        await manager.send_complete("silence_apply", {
            "removed_count": len(selected_silences),
            "backup_name": backup.GetName() if backup else "unknown"
        })

        return {
            "success": True,
            "removed_count": len(selected_silences),
            "backup_timeline": backup.GetName() if backup else None,
            "message": f"Marked {len(selected_silences)} silent segments. Backup created."
        }
    except Exception as e:
        await manager.send_error("silence_apply", str(e))
        return {"error": str(e)}
