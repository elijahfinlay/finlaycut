"""Waveform data endpoint for the transcript editor."""

import logging
import os
from fastapi import APIRouter, Query
from services.resolve_service import resolve_service
from services.audio_state import get_last_transcription_audio, unpack_last_transcription_audio
from utils.audio_utils import extract_audio, extract_audio_segment, assemble_timeline_audio
from utils.timeline_audio import prepare_timeline_analysis_audio
from api.websocket import manager
from config import settings

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

        analysis_audio_source: str | None = None
        analysis_audio_fallback_reason: str | None = None
        if bool(getattr(settings, "enable_master_mix_analysis_default", True)):
            analysis_audio = prepare_timeline_analysis_audio(
                name_prefix="finlaycut_waveform",
                fallback_restore_page="edit",
                allow_track_fallback=True,
                reuse_cached_master_mix=True,
            )
            audio_path = analysis_audio.get("audio_path")
            if not audio_path:
                return {"error": "Could not prepare timeline audio for waveform analysis"}
            analysis_audio_source = str(analysis_audio.get("analysis_audio_source") or "rendered_master_mix")
            analysis_audio_fallback_reason = (
                str(analysis_audio.get("analysis_audio_fallback_reason"))
                if analysis_audio.get("analysis_audio_fallback_reason") is not None
                else None
            )
            progress_message = (
                "Using rendered timeline mix..."
                if analysis_audio_source in {"cached_master_mix", "rendered_master_mix"}
                else "Using best-track fallback audio..."
            )
            await manager.send_progress("waveform", 0.35, progress_message)
        else:
            # Reuse audio from the last transcription if available
            signature_info = resolve_service.get_timeline_signature()
            current_timeline_signature = str(signature_info.get("timeline_signature") or "") if signature_info else ""
            cached = unpack_last_transcription_audio(get_last_transcription_audio())
            if cached and os.path.exists(cached[0]):
                audio_path, cached_duration, audio_offset, cached_signature, cached_covers_full_timeline, cached_source = cached
                signature_matches = bool(current_timeline_signature) and cached_signature == current_timeline_signature
                if cached_covers_full_timeline and signature_matches:
                    inferred_source = cached_source or (
                        "assembled_best_track" if abs(float(audio_offset)) <= 0.05 else "per_clip_best_track"
                    )
                    analysis_audio_source = "cached_master_mix" if inferred_source in {"cached_master_mix", "rendered_master_mix"} else inferred_source
                    analysis_audio_fallback_reason = None if analysis_audio_source == "cached_master_mix" else "master_mix_disabled"
                    logger.info(
                        "Reusing cached transcription audio: %s (duration=%.1fs, offset=%.1fs, source=%s)",
                        audio_path,
                        cached_duration,
                        audio_offset,
                        analysis_audio_source,
                    )
                    await manager.send_progress("waveform", 0.35, "Using transcription audio...")
                else:
                    audio_path = None
            else:
                audio_path = None

            if not audio_path:
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
                    analysis_audio_source = "rendered_master_mix"
                    analysis_audio_fallback_reason = None
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
                    analysis_audio_source = "assembled_best_track"
                    analysis_audio_fallback_reason = "master_mix_disabled"
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
            "analysis_audio_source": analysis_audio_source,
            "analysis_audio_fallback_reason": analysis_audio_fallback_reason,
        }
    except Exception as e:
        await manager.send_error("waveform", str(e))
        return {"error": str(e)}
