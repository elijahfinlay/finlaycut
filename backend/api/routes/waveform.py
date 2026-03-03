"""Waveform data endpoint for the transcript editor."""

import logging
from fastapi import APIRouter, Query
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio, extract_audio_segment
from api.websocket import manager

logger = logging.getLogger("finlaycut.waveform")
router = APIRouter()


@router.post("/analyze")
async def analyze_waveform(
    samples: int = Query(default=800, ge=100, le=4000),
):
    """
    Extract waveform amplitude data from the current timeline clip.
    Returns downsampled amplitude values for efficient frontend rendering.
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
        await manager.send_progress("waveform", 0.1, "Extracting audio...")

        full_audio = extract_audio(file_path)

        # Try to extract just the timeline-visible portion
        timeline = resolve_service.get_current_timeline()
        fps_str = timeline.GetSetting("timelineFrameRate") if timeline else None
        fps = float(fps_str) if fps_str else 24.0

        audio_path = full_audio
        try:
            source_range = resolve_service.get_timeline_item_source_range(clip)
            if source_range:
                source_start_s = source_range["source_start_frame"] / fps
                source_end_s = source_range["source_end_frame"] / fps
                audio_path = extract_audio_segment(full_audio, source_start_s, source_end_s)
        except Exception as e:
            logger.warning("Segment extraction failed (%s), using full audio", e)

        await manager.send_progress("waveform", 0.4, "Computing waveform...")

        import numpy as np
        import librosa

        y, sr = librosa.load(audio_path, sr=22050, mono=True)
        duration = len(y) / sr

        # Downsample to requested number of samples
        chunk_size = max(1, len(y) // samples)
        amplitudes = []
        for i in range(0, len(y), chunk_size):
            chunk = y[i : i + chunk_size]
            amplitudes.append(float(np.sqrt(np.mean(chunk**2))))

        # Normalize to 0-1
        max_amp = max(amplitudes) if amplitudes else 1.0
        if max_amp > 0:
            amplitudes = [a / max_amp for a in amplitudes]

        await manager.send_progress("waveform", 1.0, "Waveform ready")

        return {
            "amplitudes": amplitudes,
            "duration": duration,
            "sample_count": len(amplitudes),
            "sample_rate": sr,
        }
    except Exception as e:
        await manager.send_error("waveform", str(e))
        return {"error": str(e)}
