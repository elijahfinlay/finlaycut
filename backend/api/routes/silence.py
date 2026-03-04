"""Silence removal endpoints."""

import logging
from fastapi import APIRouter
from models.silence import SilenceConfig, SilenceApplyRequest
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from utils.timeline_audio import assemble_current_timeline_audio
from api.websocket import manager

logger = logging.getLogger("finlaycut.silence")

router = APIRouter()


def _merge_frame_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    valid = sorted((int(start), int(end)) for start, end in ranges if int(end) > int(start))
    if not valid:
        return []

    merged = [valid[0]]
    for start, end in valid[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _subtract_frame_ranges(
    base_start: int,
    base_end: int,
    cuts: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if base_end <= base_start:
        return []

    if not cuts:
        return [(base_start, base_end)]

    remaining: list[tuple[int, int]] = []
    cursor = int(base_start)
    for cut_start, cut_end in cuts:
        if cut_end <= cursor:
            continue
        if cut_start >= base_end:
            break
        if cut_start > cursor:
            remaining.append((cursor, min(cut_start, base_end)))
        cursor = max(cursor, cut_end)
        if cursor >= base_end:
            break

    if cursor < base_end:
        remaining.append((cursor, base_end))

    return remaining


def _frame_range_to_segment(start_frame: int, end_frame: int, timeline_start_frame: int, fps: float) -> dict:
    return {
        "start_frame": int(start_frame),
        "end_frame": int(end_frame),
        "start": (int(start_frame) - int(timeline_start_frame)) / float(fps),
        "end": (int(end_frame) - int(timeline_start_frame)) / float(fps),
        "duration": max(0.0, (int(end_frame) - int(start_frame)) / float(fps)),
        "selected": True,
    }


def _merge_detected_segments(segments: list[dict]) -> list[dict]:
    normalized = []
    for seg in segments:
        start_frame = seg.get("start_frame")
        end_frame = seg.get("end_frame")
        if start_frame is None or end_frame is None or int(end_frame) <= int(start_frame):
            continue
        normalized.append({
            "start_frame": int(start_frame),
            "end_frame": int(end_frame),
            "kind": seg.get("kind"),
            "boundary_source": seg.get("boundary_source"),
        })

    if not normalized:
        return []

    normalized.sort(key=lambda seg: (seg["start_frame"], seg["end_frame"]))
    merged = [normalized[0]]
    for seg in normalized[1:]:
        prev = merged[-1]
        if seg["start_frame"] <= prev["end_frame"] and seg.get("kind") == prev.get("kind"):
            prev["end_frame"] = max(prev["end_frame"], seg["end_frame"])
            if seg.get("boundary_source") == "waveform" or prev.get("boundary_source") == "waveform":
                prev["boundary_source"] = "waveform"
        else:
            merged.append(seg)
    return merged


def _trim_segment_by_padding(segment: dict, padding_ms: int) -> dict | None:
    padding_seconds = max(0, int(padding_ms)) / 1000.0
    start = float(segment.get("start", 0.0)) + padding_seconds
    end = float(segment.get("end", 0.0)) - padding_seconds
    if end <= start:
        return None

    updated = dict(segment)
    updated["start"] = round(start, 4)
    updated["end"] = round(end, 4)
    updated["duration"] = round(updated["end"] - updated["start"], 4)
    return updated


@router.post("/analyze")
@heavy_operation("Silence Analysis")
async def analyze_silence(config: SilenceConfig):
    """
    Analyze the current timeline for silent segments.
    Extracts audio from ALL clips, runs silence detection, returns segments.
    """
    # Get ALL clips — prefer audio track 1, fall back to video track 1
    clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        return {"error": "No clips found in timeline. Make sure your timeline has at least one clip."}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate") if timeline else None
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = timeline.GetStartFrame() if timeline else 0
    effective_min_duration_ms = max(
        int(config.min_duration_ms),
        int(round((max(14, int(config.min_duration_frames)) / fps) * 1000.0)),
    )

    logger.info("Silence analysis: %d clips on timeline", len(clips))

    await manager.send_progress("silence", 0.02, f"Extracting audio from {len(clips)} clip(s)...")

    try:
        audio_cache: dict[str, str] = {}  # source file -> extracted WAV path
        all_silences = []

        for clip_idx, clip in enumerate(clips):
            file_path = resolve_service.get_clip_file_path(clip)
            if not file_path:
                logger.warning("Clip %d has no file path, skipping", clip_idx)
                continue

            clip_start_frame = int(clip.GetStart())
            clip_end_frame = int(clip.GetEnd())
            clip_timeline_offset = (clip_start_frame - timeline_start_frame) / fps

            # Progress: extraction phase uses 2-15% range
            pct = 0.02 + (clip_idx / len(clips)) * 0.13
            await manager.send_progress("silence", pct,
                f"Extracting audio ({clip_idx + 1}/{len(clips)} clips)...")

            # Extract full audio from source file (cached per unique file)
            if file_path not in audio_cache:
                audio_cache[file_path] = extract_audio(file_path)
            full_audio = audio_cache[file_path]

            # Extract just the source range visible in the timeline
            source_range = resolve_service.get_timeline_item_source_range(clip)
            if source_range:
                src_start = source_range["source_start_frame"] / fps
                src_end = source_range["source_end_frame"] / fps
                try:
                    audio_path = extract_audio_segment(full_audio, src_start, src_end)
                except Exception as e:
                    logger.warning("Segment extraction failed for clip %d (%s), using full audio", clip_idx, e)
                    audio_path = full_audio
            else:
                # Fallback: try left offset extraction
                try:
                    left_offset = clip.GetLeftOffset()
                    clip_duration_frames = clip_end_frame - clip_start_frame
                    source_start = left_offset / fps
                    source_end = (left_offset + clip_duration_frames) / fps
                    audio_path = extract_audio_segment(full_audio, source_start, source_end)
                except Exception as e:
                    logger.warning("Segment extraction failed for clip %d (%s), using full audio", clip_idx, e)
                    audio_path = full_audio

            # Detect silences with progress scaled across clips
            # Detection phase uses 15-90% range, split evenly across clips
            clip_progress_start = 0.15 + (clip_idx / len(clips)) * 0.75
            clip_progress_range = 0.75 / len(clips)

            async def progress_cb(pct, msg, _start=clip_progress_start, _range=clip_progress_range):
                scaled = _start + pct * _range
                await manager.send_progress("silence", scaled, msg)

            silences = await audio_service.detect_silence_with_progress(
                audio_path,
                threshold_db=config.threshold_db,
                min_duration_ms=effective_min_duration_ms,
                progress_fn=progress_cb,
            )
            profile = audio_service.build_waveform_profile(audio_path)
            silences = audio_service.refine_silence_segments_to_waveform(
                audio_path,
                silences,
                min_duration_ms=max(20, int(effective_min_duration_ms * 0.25)),
                profile=profile,
            )

            quiet_speech_segments = audio_service.detect_low_prominence_speech(
                audio_path,
                threshold_db=config.threshold_db,
                min_duration_ms=max(250, int(effective_min_duration_ms * 0.85)),
                quiet_delta_db=12.0,
            )
            quiet_speech_segments = audio_service.refine_segments_to_low_energy(
                audio_path,
                quiet_speech_segments,
                max_shift_ms=40,
                min_duration_ms=max(120, int(effective_min_duration_ms * 0.5)),
                profile=profile,
            )
            for segment in quiet_speech_segments:
                segment["boundary_source"] = "waveform"
            candidate_segments = [*silences, *quiet_speech_segments]

            # Offset silence timestamps to timeline time
            for s in candidate_segments:
                s["start"] += clip_timeline_offset
                s["end"] += clip_timeline_offset
                s["start_frame"] = clip_start_frame + int(round((s["start"] - clip_timeline_offset) * fps))
                s["end_frame"] = clip_start_frame + int(round((s["end"] - clip_timeline_offset) * fps))

            all_silences.extend(candidate_segments)

        merged_segments = _merge_detected_segments([
            {
                **s,
                "start_frame": int(s.get("start_frame", timeline_start_frame + int(round(s["start"] * fps)))),
                "end_frame": int(s.get("end_frame", timeline_start_frame + int(round(s["end"] * fps)))),
            }
            for s in all_silences
        ])
        all_silences = []
        for seg in merged_segments:
            result_seg = _frame_range_to_segment(
                seg["start_frame"], seg["end_frame"], timeline_start_frame, fps
            )
            if seg.get("kind"):
                result_seg["kind"] = seg["kind"]
            if seg.get("boundary_source"):
                result_seg["boundary_source"] = seg["boundary_source"]
            all_silences.append(result_seg)

        total_silence = sum(s["duration"] for s in all_silences)

        await manager.send_progress("silence", 1.0, f"Found {len(all_silences)} silent segments")

        return {
            "segments": all_silences,
            "total_silence_seconds": total_silence,
            "segment_count": len(all_silences),
            "effective_min_duration_ms": effective_min_duration_ms,
        }
    except Exception as e:
        await manager.send_error("silence", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_silence_removal(request: SilenceApplyRequest):
    """
    Apply silence removal to the current timeline.
    Creates a backup first, then edits the active timeline in place without the
    selected silent segments.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    selected_silences = [s for s in request.segments if s.selected]
    if not selected_silences:
        return {"error": "No silent segments selected for removal"}

    await manager.send_progress("silence_apply", 0.1, "Creating backup...")

    # Create backup timeline (non-blocking — proceed even if backup fails)
    backup = resolve_service.duplicate_timeline()
    backup_name = "none"
    if backup is not None:
        try:
            backup_name = backup.GetName()
        except Exception:
            backup_name = "backup"
    else:
        logger.warning("Could not create backup timeline")

    await manager.send_progress("silence_apply", 0.3, "Calculating kept segments...")

    try:
        timeline = resolve_service.get_current_timeline()
        if timeline is None:
            return {"error": "No active timeline"}

        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0

        # Get total timeline duration in seconds
        tl_start = timeline.GetStartFrame()
        tl_end = timeline.GetEndFrame()
        total_duration = (tl_end - tl_start) / fps

        await manager.send_progress("silence_apply", 0.3, "Assembling current timeline audio...")
        assembled_audio, assembled_fps, assembled_tl_start, _assembled_tl_end = assemble_current_timeline_audio()
        if not assembled_audio or assembled_fps is None or assembled_tl_start is None:
            return {"error": "Could not assemble current timeline audio"}

        await manager.send_progress("silence_apply", 0.4, "Refining pause boundaries...")
        profile = audio_service.build_waveform_profile(assembled_audio)
        silence_candidates: list[dict] = []
        quiet_candidates: list[dict] = []
        for silence in selected_silences:
            segment = {
                "start": float(silence.start),
                "end": float(silence.end),
                "duration": float(silence.duration),
                "selected": bool(silence.selected),
                "kind": silence.kind,
                "start_frame": silence.start_frame,
                "end_frame": silence.end_frame,
                "boundary_source": getattr(silence, "boundary_source", None),
            }
            if silence.kind == "low_prominence_speech":
                quiet_candidates.append(segment)
            else:
                silence_candidates.append(segment)

        refined_segments = audio_service.refine_silence_segments_to_waveform(
            assembled_audio,
            silence_candidates,
            preserve_padding_ms=request.padding_ms,
            profile=profile,
        )
        refined_quiet = audio_service.refine_segments_to_low_energy(
            assembled_audio,
            quiet_candidates,
            max_shift_ms=40,
            min_duration_ms=120,
            profile=profile,
        )
        for segment in refined_quiet:
            segment["boundary_source"] = "waveform"
        refined_segments.extend(
            trimmed
            for trimmed in (_trim_segment_by_padding(segment, request.padding_ms) for segment in refined_quiet)
            if trimmed is not None
        )

        cut_frame_ranges = []
        for segment in refined_segments:
            start_frame = int(assembled_tl_start) + int(round(float(segment["start"]) * assembled_fps))
            end_frame = int(assembled_tl_start) + int(round(float(segment["end"]) * assembled_fps))
            start_frame = max(tl_start, min(tl_end, start_frame))
            end_frame = max(tl_start, min(tl_end, end_frame))

            if end_frame > start_frame:
                cut_frame_ranges.append((start_frame, end_frame))

        if not cut_frame_ranges:
            return {"error": "Selected pauses no longer have removable dead air after waveform refinement"}

        cut_frame_ranges = _merge_frame_ranges(cut_frame_ranges)
        kept_frame_ranges = _subtract_frame_ranges(tl_start, tl_end, cut_frame_ranges)
        kept_segments = [
            _frame_range_to_segment(start_frame, end_frame, tl_start, fps)
            for start_frame, end_frame in kept_frame_ranges
        ]

        if not kept_segments:
            return {"error": "Nothing left to keep after removing all silences"}

        await manager.send_progress("silence_apply", 0.5, "Applying edits to current timeline...")

        result = resolve_service.rebuild_timeline_keeping(kept_segments, in_place=True)

        if not result["success"]:
            return {"error": result.get("error", "Failed to apply edits to current timeline")}

        total_removed = sum((end_frame - start_frame) / fps for start_frame, end_frame in cut_frame_ranges)

        await manager.send_progress("silence_apply", 1.0, "Done")
        await manager.send_complete("silence_apply", {
            "removed_count": len(cut_frame_ranges),
            "total_removed": round(total_removed, 1),
            "backup_name": backup_name
        })

        # Record in undo history
        from api.routes.undo import _record
        _record("Silence Removal", backup_name,
                f"Removed {len(cut_frame_ranges)} silence sections ({total_removed:.1f}s)")

        return {
            "success": True,
            "removed_count": len(cut_frame_ranges),
            "total_removed_seconds": round(total_removed, 1),
            "backup_timeline": backup_name,
            "timeline_name": result.get("timeline_name"),
            "message": f"Removed {len(cut_frame_ranges)} silence sections ({total_removed:.1f}s) on the current timeline. Backup: {backup_name}"
        }
    except Exception as e:
        await manager.send_error("silence_apply", str(e))
        return {"error": str(e)}
