"""Waveform data endpoint for the transcript editor."""

import logging
import os
from fastapi import APIRouter, Query
from services.resolve_service import resolve_service
from services.audio_state import get_last_transcription_audio
from utils.audio_utils import extract_audio, extract_audio_segment, assemble_timeline_audio
from api.websocket import manager

logger = logging.getLogger("finlaycut.waveform")
router = APIRouter()


@router.post("/analyze")
async def analyze_waveform(
    samples: int = Query(default=800, ge=100, le=4000),
):
    """
    Extract waveform amplitude data from the full current timeline.
    Iterates all clips on audio track 1 (or video track 1) and assembles
    a single waveform covering the entire timeline duration.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    try:
        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0
        timeline_start_frame = int(timeline.GetStartFrame())
        timeline_end_frame = int(timeline.GetEndFrame())
        timeline_duration_seconds = (timeline_end_frame - timeline_start_frame) / fps

        if timeline_duration_seconds <= 0:
            return {"error": "Timeline has no duration"}

        # Reuse audio from the last transcription if available
        cached = get_last_transcription_audio()
        if cached and os.path.exists(cached[0]):
            audio_path, duration = cached
            logger.info("Reusing cached transcription audio: %s (%.1fs)", audio_path, duration)
            await manager.send_progress("waveform", 0.35, "Using transcription audio...")

            import numpy as np
            import librosa

            await manager.send_progress("waveform", 0.50, "Computing waveform...")
            y, sr = librosa.load(audio_path, sr=22050, mono=True)

            expected_samples = int(duration * sr)
            if len(y) > expected_samples:
                y = y[:expected_samples]

            indices = np.linspace(0, len(y), samples + 1, dtype=int)
            amplitudes = []
            for i in range(samples):
                chunk = y[indices[i]:indices[i+1]]
                amplitudes.append(float(np.sqrt(np.mean(chunk**2))) if len(chunk) > 0 else 0.0)

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

        clip_selection = resolve_service.get_timeline_audio_clip_contexts()
        clip_contexts = clip_selection["contexts"]
        if not clip_selection["has_any_items"]:
            return {"error": "No clips found in timeline"}
        if not clip_contexts:
            logger.warning("Waveform analysis found no usable clip sources: %s", clip_selection["attempts"])
            await manager.send_progress("waveform", 0.20, "Rendering timeline audio in Resolve...")
            audio_path = resolve_service.render_current_timeline_audio(
                "finlaycut_waveform",
                fallback_restore_page="edit",
            )
            if not audio_path:
                return {"error": "Could not extract audio from any timeline clips"}
        else:
            await manager.send_progress("waveform", 0.05, f"Extracting audio from {len(clip_contexts)} clip(s)...")

            audio_cache: dict[str, str] = {}
            clip_segments: list[dict] = []

            for i, clip_context in enumerate(clip_contexts):
                file_path = str(clip_context["file_path"])
                clip_timeline_start_sec = float(clip_context["clip_timeline_offset"])
                clip_duration_sec = float(clip_context["clip_duration_seconds"])

                pct = 0.05 + (i / len(clip_contexts)) * 0.25
                await manager.send_progress("waveform", pct, f"Extracting audio ({i + 1}/{len(clip_contexts)})...")

                if file_path not in audio_cache:
                    audio_cache[file_path] = extract_audio(file_path)
                full_audio = audio_cache[file_path]

                source_start_seconds = clip_context.get("source_start_seconds")
                source_end_seconds = clip_context.get("source_end_seconds")
                if source_start_seconds is not None and source_end_seconds is not None:
                    try:
                        clip_audio = extract_audio_segment(
                            full_audio,
                            float(source_start_seconds),
                            float(source_end_seconds),
                        )
                    except Exception as e:
                        logger.warning("Segment extraction failed for clip %d (%s), using full audio", i, e)
                        clip_audio = full_audio
                else:
                    clip_audio = full_audio

                clip_segments.append({
                    "audio_path": clip_audio,
                    "timeline_start_seconds": clip_timeline_start_sec,
                    "duration_seconds": clip_duration_sec,
                })

            if not clip_segments:
                return {"error": "Could not extract audio from any timeline clips"}

            await manager.send_progress("waveform", 0.35, "Assembling timeline audio...")

            audio_path = assemble_timeline_audio(clip_segments, timeline_duration_seconds)
        duration = timeline_duration_seconds

        await manager.send_progress("waveform", 0.50, "Computing waveform...")

        import numpy as np
        import librosa

        y, sr = librosa.load(audio_path, sr=22050, mono=True)

        # Clamp to timeline duration so waveform never exceeds it
        expected_samples = int(timeline_duration_seconds * sr)
        if len(y) > expected_samples:
            y = y[:expected_samples]
        duration = timeline_duration_seconds

        # Produce exactly `samples` amplitudes using linspace
        indices = np.linspace(0, len(y), samples + 1, dtype=int)
        amplitudes = []
        for i in range(samples):
            chunk = y[indices[i]:indices[i+1]]
            amplitudes.append(float(np.sqrt(np.mean(chunk**2))) if len(chunk) > 0 else 0.0)

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
