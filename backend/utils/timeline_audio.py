"""Helpers for assembling the audible Resolve timeline into one WAV file."""

from __future__ import annotations

import logging

from services.resolve_service import resolve_service
from utils.audio_utils import assemble_timeline_audio, extract_audio, extract_audio_segment

logger = logging.getLogger("finlaycut.timeline_audio")


def assemble_current_timeline_audio() -> tuple[str | None, float | None, int | None, int | None]:
    """Assemble the current timeline's audible track content into one WAV file."""
    if not resolve_service.connected:
        return None, None, None, None

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return None, None, None, None

    clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        return None, None, None, None

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())
    timeline_duration_seconds = max(0.0, (timeline_end_frame - timeline_start_frame) / fps)

    audio_cache: dict[str, str] = {}
    clip_segments: list[dict] = []
    for clip in clips:
        clip_context = resolve_service.get_clip_audio_context(clip)
        if clip_context is None:
            continue

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
