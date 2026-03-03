"""Speaker diarization endpoints."""

import logging
from fastapi import APIRouter, Query
from typing import Optional
from services.resolve_service import resolve_service
from services.diarization_service import diarization_service
from utils.audio_utils import extract_audio
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.diarization_route")
router = APIRouter()


@router.post("/analyze")
@heavy_operation("Speaker Diarization")
async def analyze_speakers(
    num_speakers: Optional[int] = Query(default=None, ge=2, le=10),
):
    """
    Identify speakers in the current timeline audio.
    Returns speaker segments with timestamps.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found at playhead"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("diarization", 0.05, "Extracting audio...")
        audio_path = extract_audio(file_path)

        async def progress_fn(pct, msg):
            await manager.send_progress("diarization", pct, msg)

        segments = await diarization_service.diarize_with_progress(
            audio_path,
            num_speakers=num_speakers,
            progress_fn=progress_fn,
        )

        speakers = list(dict.fromkeys(s["speaker"] for s in segments))

        return {
            "segments": segments,
            "speakers": speakers,
            "speaker_count": len(speakers),
            "segment_count": len(segments),
        }
    except ImportError as e:
        return {"error": str(e)}
    except Exception as e:
        await manager.send_error("diarization", str(e))
        return {"error": str(e)}
