"""Helpers for assembling the audible Resolve timeline into one WAV file."""

from __future__ import annotations

import logging
import os

from config import settings
from services.audio_state import get_last_transcription_audio, unpack_last_transcription_audio
from services.resolve_service import resolve_service
from utils.audio_utils import assemble_timeline_audio, extract_audio, extract_audio_segment

logger = logging.getLogger("finlaycut.timeline_audio")


def _current_timeline_frames() -> tuple[float | None, int | None, int | None]:
    if not resolve_service.connected:
        return None, None, None

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return None, None, None

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())
    return fps, timeline_start_frame, timeline_end_frame


def _matching_cached_master_mix() -> tuple[str, float, int, int] | None:
    fps, timeline_start_frame, timeline_end_frame = _current_timeline_frames()
    if fps is None or timeline_start_frame is None or timeline_end_frame is None:
        return None

    cached = unpack_last_transcription_audio(get_last_transcription_audio())
    if cached is None:
        return None

    audio_path, _duration, _offset, cached_signature, covers_full_timeline, cached_source = cached
    signature_info = resolve_service.get_timeline_signature()
    current_signature = str(signature_info.get("timeline_signature") or "") if signature_info else ""
    if not current_signature or cached_signature != current_signature or not covers_full_timeline:
        return None
    if cached_source not in {"cached_master_mix", "rendered_master_mix"}:
        return None
    if not os.path.exists(audio_path):
        return None

    return audio_path, float(fps), int(timeline_start_frame), int(timeline_end_frame)


def assemble_current_timeline_audio(
    allow_render_fallback: bool = True,
) -> tuple[str | None, float | None, int | None, int | None]:
    """Assemble the current timeline's audible track content into one WAV file."""
    if not resolve_service.connected:
        return None, None, None, None

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return None, None, None, None

    selection = resolve_service.get_timeline_audio_clip_contexts()
    clip_contexts = selection["contexts"]
    if not selection["has_any_items"]:
        return None, None, None, None

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())
    timeline_duration_seconds = max(0.0, (timeline_end_frame - timeline_start_frame) / fps)

    if not clip_contexts:
        if allow_render_fallback:
            rendered_audio = resolve_service.render_current_timeline_audio(
                fallback_restore_page="edit",
            )
            if rendered_audio:
                return rendered_audio, fps, timeline_start_frame, timeline_end_frame
        return None, fps, timeline_start_frame, timeline_end_frame

    audio_cache: dict[str, str] = {}
    clip_segments: list[dict] = []
    for clip_context in clip_contexts:
        file_path = str(clip_context["file_path"])
        if file_path not in audio_cache:
            audio_cache[file_path] = extract_audio(file_path)
        full_audio = audio_cache[file_path]

        visible_audio = full_audio
        if clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
            try:
                visible_audio = extract_audio_segment(
                    full_audio,
                    float(clip_context["source_start_seconds"]),
                    float(clip_context["source_end_seconds"]),
                )
            except Exception as exc:
                logger.warning("Timeline segment extraction failed (%s), using full audio", exc)

        clip_segments.append(
            {
                "audio_path": visible_audio,
                "timeline_start_seconds": float(clip_context["clip_timeline_offset"]),
                "duration_seconds": float(clip_context["clip_duration_seconds"]),
            }
        )

    if not clip_segments:
        return None, fps, timeline_start_frame, timeline_end_frame

    assembled_audio = assemble_timeline_audio(clip_segments, timeline_duration_seconds)
    return assembled_audio, fps, timeline_start_frame, timeline_end_frame


def prepare_timeline_analysis_audio(
    *,
    name_prefix: str = "finlaycut_analysis_audio",
    mark_in_frame: int | None = None,
    mark_out_frame: int | None = None,
    fallback_restore_page: str = "edit",
    allow_track_fallback: bool = True,
    reuse_cached_master_mix: bool = True,
) -> dict[str, str | float | int | None]:
    fps, timeline_start_frame, timeline_end_frame = _current_timeline_frames()
    if fps is None or timeline_start_frame is None or timeline_end_frame is None:
        return {
            "audio_path": None,
            "fps": None,
            "timeline_start_frame": None,
            "timeline_end_frame": None,
            "analysis_audio_source": None,
            "analysis_audio_fallback_reason": None,
        }

    fallback_reason: str | None = None

    if bool(getattr(settings, "enable_master_mix_analysis_default", True)):
        if reuse_cached_master_mix and mark_in_frame is None and mark_out_frame is None:
            cached_master_mix = _matching_cached_master_mix()
            if cached_master_mix is not None:
                audio_path, cached_fps, cached_start_frame, cached_end_frame = cached_master_mix
                return {
                    "audio_path": audio_path,
                    "fps": cached_fps,
                    "timeline_start_frame": cached_start_frame,
                    "timeline_end_frame": cached_end_frame,
                    "analysis_audio_source": "cached_master_mix",
                    "analysis_audio_fallback_reason": None,
                }

        rendered_audio = resolve_service.render_current_timeline_audio(
            name_prefix=name_prefix,
            mark_in_frame=mark_in_frame,
            mark_out_frame=mark_out_frame,
            fallback_restore_page=fallback_restore_page,
        )
        if rendered_audio:
            return {
                "audio_path": rendered_audio,
                "fps": float(fps),
                "timeline_start_frame": int(timeline_start_frame),
                "timeline_end_frame": int(timeline_end_frame),
                "analysis_audio_source": "rendered_master_mix",
                "analysis_audio_fallback_reason": None,
            }

        fallback_reason = "master_mix_render_unavailable"
        logger.warning("Timeline analysis fell back because master-mix render was unavailable")

    if not allow_track_fallback:
        return {
            "audio_path": None,
            "fps": float(fps),
            "timeline_start_frame": int(timeline_start_frame),
            "timeline_end_frame": int(timeline_end_frame),
            "analysis_audio_source": None,
            "analysis_audio_fallback_reason": fallback_reason or "track_fallback_disabled",
        }

    assembled_audio, assembled_fps, assembled_start_frame, assembled_end_frame = assemble_current_timeline_audio(
        allow_render_fallback=False,
    )
    if assembled_audio:
        return {
            "audio_path": assembled_audio,
            "fps": float(assembled_fps),
            "timeline_start_frame": int(assembled_start_frame),
            "timeline_end_frame": int(assembled_end_frame),
            "analysis_audio_source": "assembled_best_track",
            "analysis_audio_fallback_reason": fallback_reason or "master_mix_disabled",
        }

    return {
        "audio_path": None,
        "fps": float(fps),
        "timeline_start_frame": int(timeline_start_frame),
        "timeline_end_frame": int(timeline_end_frame),
        "analysis_audio_source": None,
        "analysis_audio_fallback_reason": fallback_reason or "track_audio_unavailable",
    }


def get_precision_timeline_audio(
    name_prefix: str = "finlaycut_precision_audio",
    fallback_restore_page: str = "edit",
) -> tuple[str | None, float | None, int | None, int | None]:
    """
    Return the closest practical master-timeline audio source for cut precision.

    For destructive edits we prefer a rendered audible timeline mix so boundary
    refinement hears the same combined audio the user hears in Resolve. If that
    render path is unavailable, we fall back to the existing assembled-track
    approximation.
    """
    if not resolve_service.connected:
        return None, None, None, None

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return None, None, None, None

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())

    rendered_audio = resolve_service.render_current_timeline_audio(
        name_prefix=name_prefix,
        fallback_restore_page=fallback_restore_page,
    )
    if rendered_audio:
        return rendered_audio, fps, timeline_start_frame, timeline_end_frame

    logger.warning("Falling back to assembled timeline audio for precision cut refinement")
    return assemble_current_timeline_audio()
