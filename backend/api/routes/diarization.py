"""Speaker diarization endpoints."""

import logging
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.diarization_service import diarization_service
from utils.audio_utils import extract_audio, extract_audio_segment, assemble_timeline_audio
from utils.operation_queue import heavy_operation
from utils.speaker_analysis import build_speaker_summaries, compute_speaker_loudness
from utils.timeline_audio import prepare_timeline_analysis_audio
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.diarization_route")
router = APIRouter()


class TranscriptSpeakerRequest(BaseModel):
    words: list[dict]
    num_speakers: Optional[int] = None


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

    try:
        analysis_audio_source: str | None = None
        analysis_audio_fallback_reason: str | None = None
        if bool(getattr(settings, "enable_master_mix_analysis_default", True)):
            await manager.send_progress("diarization", 0.05, "Preparing timeline audio...")
            analysis_audio = prepare_timeline_analysis_audio(
                name_prefix="finlaycut_diarization",
                fallback_restore_page="edit",
                allow_track_fallback=True,
                reuse_cached_master_mix=True,
            )
            audio_path = analysis_audio.get("audio_path")
            if not audio_path:
                return {"error": "Could not extract timeline audio for speaker analysis"}
            analysis_audio_source = str(analysis_audio.get("analysis_audio_source") or "rendered_master_mix")
            analysis_audio_fallback_reason = (
                str(analysis_audio.get("analysis_audio_fallback_reason"))
                if analysis_audio.get("analysis_audio_fallback_reason") is not None
                else None
            )
        else:
            clip = resolve_service.get_clip_at_playhead("audio", 1)
            if clip is None:
                clip = resolve_service.get_clip_at_playhead("video", 1)
            if clip is None:
                return {"error": "No clips found at playhead"}

            file_path = resolve_service.get_clip_file_path(clip)
            if not file_path:
                return {"error": "Could not determine source file path"}
            await manager.send_progress("diarization", 0.05, "Extracting audio...")
            audio_path = extract_audio(file_path)
            analysis_audio_source = "per_clip_best_track"
            analysis_audio_fallback_reason = "master_mix_disabled"

        async def progress_fn(pct, msg):
            await manager.send_progress("diarization", pct, msg)

        segments, method, fallback_reason = await diarization_service.diarize_with_progress(
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
            "method": method,
            "fallback_reason": fallback_reason,
            "analysis_audio_source": analysis_audio_source,
            "analysis_audio_fallback_reason": analysis_audio_fallback_reason,
        }
    except ImportError as e:
        return {"error": str(e)}
    except Exception as e:
        await manager.send_error("diarization", str(e))
        return {"error": str(e)}


@router.post("/annotate-transcript")
@heavy_operation("Speaker Diarization")
async def annotate_transcript_speakers(request: TranscriptSpeakerRequest):
    """
    Diarize the current timeline audio and assign speaker labels to transcript words.
    Returns annotated words plus per-speaker duration/loudness summaries so the UI
    can offer "keep this speaker / cut that speaker" controls.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not request.words:
        return {"error": "No transcript words provided"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    clip_selection = resolve_service.get_timeline_audio_clip_contexts()
    clip_contexts = clip_selection["contexts"]
    if not clip_selection["has_any_items"]:
        return {"error": "No clips found in timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())
    timeline_duration_seconds = (timeline_end_frame - timeline_start_frame) / fps

    try:
        await manager.send_progress("diarization", 0.04, "Extracting timeline audio...")
        analysis_audio_source: str | None = None
        analysis_audio_fallback_reason: str | None = None
        if bool(getattr(settings, "enable_master_mix_analysis_default", True)):
            analysis_audio = prepare_timeline_analysis_audio(
                name_prefix="finlaycut_diarization",
                fallback_restore_page="edit",
                allow_track_fallback=True,
                reuse_cached_master_mix=True,
            )
            assembled_audio = analysis_audio.get("audio_path")
            if not assembled_audio:
                return {"error": "Could not extract timeline audio for speaker analysis"}
            analysis_audio_source = str(analysis_audio.get("analysis_audio_source") or "rendered_master_mix")
            analysis_audio_fallback_reason = (
                str(analysis_audio.get("analysis_audio_fallback_reason"))
                if analysis_audio.get("analysis_audio_fallback_reason") is not None
                else None
            )
        else:
            if not clip_contexts:
                logger.warning("Speaker analysis found no usable clip sources: %s", clip_selection["attempts"])
                assembled_audio = resolve_service.render_current_timeline_audio(
                    "finlaycut_diarization",
                    fallback_restore_page="edit",
                )
                if not assembled_audio:
                    return {"error": "Could not extract timeline audio for speaker analysis"}
                analysis_audio_source = "rendered_master_mix"
                analysis_audio_fallback_reason = None
            else:
                audio_cache: dict[str, str] = {}
                clip_segments: list[dict] = []

                for index, clip_context in enumerate(clip_contexts):
                    file_path = str(clip_context["file_path"])
                    if file_path not in audio_cache:
                        audio_cache[file_path] = extract_audio(file_path)
                    full_audio = audio_cache[file_path]

                    audio_path = full_audio
                    if clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
                        try:
                            audio_path = extract_audio_segment(
                                full_audio,
                                float(clip_context["source_start_seconds"]),
                                float(clip_context["source_end_seconds"]),
                            )
                        except Exception as e:
                            logger.warning("Speaker segment extraction failed for clip %d (%s)", index, e)

                    clip_segments.append({
                        "audio_path": audio_path,
                        "timeline_start_seconds": float(clip_context["clip_timeline_offset"]),
                        "duration_seconds": float(clip_context["clip_duration_seconds"]),
                    })

                if not clip_segments:
                    return {"error": "Could not extract timeline audio for speaker analysis"}

                assembled_audio = assemble_timeline_audio(clip_segments, timeline_duration_seconds)
                analysis_audio_source = "assembled_best_track"
                analysis_audio_fallback_reason = "master_mix_disabled"

        async def progress_fn(pct, msg):
            scaled = 0.10 + pct * 0.78
            await manager.send_progress("diarization", scaled, msg)

        diarization_segments, method, fallback_reason = await diarization_service.diarize_with_progress(
            assembled_audio,
            num_speakers=request.num_speakers,
            progress_fn=progress_fn,
        )

        words = diarization_service.assign_speakers_to_words(
            [dict(word) for word in request.words],
            diarization_segments,
        )
        loudness = compute_speaker_loudness(assembled_audio, diarization_segments)
        speakers = build_speaker_summaries(diarization_segments, words, loudness)

        await manager.send_progress("diarization", 1.0, f"Found {len(speakers)} speaker profiles")
        return {
            "words": words,
            "segments": diarization_segments,
            "speakers": speakers,
            "speaker_count": len(speakers),
            "method": method,
            "fallback_reason": fallback_reason,
            "analysis_audio_source": analysis_audio_source,
            "analysis_audio_fallback_reason": analysis_audio_fallback_reason,
        }
    except ImportError as e:
        return {"error": str(e)}
    except Exception as e:
        await manager.send_error("diarization", str(e))
        return {"error": str(e)}
