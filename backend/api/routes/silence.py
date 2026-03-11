"""Silence removal endpoints."""

import logging
from fastapi import APIRouter
from models.silence import SilenceConfig, SilenceApplyRequest
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.cut_refiner_service import cut_refiner_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from utils.timeline_audio import prepare_timeline_analysis_audio
from api.websocket import manager
from config import settings

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


async def _analyze_silence_per_clip_fallback(
    clips: list,
    *,
    config: SilenceConfig,
    fps: float,
    timeline_start_frame: int,
    effective_min_duration_ms: int,
) -> list[dict]:
    audio_cache: dict[str, str] = {}
    all_silences: list[dict] = []

    await manager.send_progress("silence", 0.02, f"Extracting audio from {len(clips)} clip(s)...")

    for clip_idx, clip in enumerate(clips):
        file_path = resolve_service.get_clip_file_path(clip)
        if not file_path:
            logger.warning("Clip %d has no file path, skipping", clip_idx)
            continue

        clip_start_frame = int(clip.GetStart())
        clip_end_frame = int(clip.GetEnd())
        clip_timeline_offset = (clip_start_frame - timeline_start_frame) / fps

        pct = 0.02 + (clip_idx / len(clips)) * 0.13
        await manager.send_progress("silence", pct, f"Extracting audio ({clip_idx + 1}/{len(clips)} clips)...")

        if file_path not in audio_cache:
            audio_cache[file_path] = extract_audio(file_path)
        full_audio = audio_cache[file_path]

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
            try:
                left_offset = clip.GetLeftOffset()
                clip_duration_frames = clip_end_frame - clip_start_frame
                source_start = left_offset / fps
                source_end = (left_offset + clip_duration_frames) / fps
                audio_path = extract_audio_segment(full_audio, source_start, source_end)
            except Exception as e:
                logger.warning("Segment extraction failed for clip %d (%s), using full audio", clip_idx, e)
                audio_path = full_audio

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
        candidate_segments = [*silences, *quiet_speech_segments]

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
    result_segments = []
    for seg in merged_segments:
        result_seg = _frame_range_to_segment(seg["start_frame"], seg["end_frame"], timeline_start_frame, fps)
        if seg.get("kind"):
            result_seg["kind"] = seg["kind"]
        if seg.get("boundary_source"):
            result_seg["boundary_source"] = seg["boundary_source"]
        if seg.get("refine_confidence") is not None:
            result_seg["refine_confidence"] = seg.get("refine_confidence")
        if seg.get("refine_reason") is not None:
            result_seg["refine_reason"] = seg.get("refine_reason")
        result_seg["used_fallback_seed"] = bool(seg.get("used_fallback_seed", False))
        result_segments.append(result_seg)
    return result_segments


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

    try:
        analysis_audio_source: str | None = None
        analysis_audio_fallback_reason: str | None = None
        if bool(getattr(settings, "enable_master_mix_analysis_default", True)):
            await manager.send_progress("silence", 0.05, "Rendering timeline mix for silence analysis...")
            analysis_audio = prepare_timeline_analysis_audio(
                name_prefix="finlaycut_silence",
                fallback_restore_page="edit",
                allow_track_fallback=False,
                reuse_cached_master_mix=True,
            )
            if analysis_audio.get("audio_path"):
                audio_path = str(analysis_audio["audio_path"])
                analysis_audio_source = str(analysis_audio.get("analysis_audio_source") or "rendered_master_mix")
                analysis_audio_fallback_reason = (
                    str(analysis_audio.get("analysis_audio_fallback_reason"))
                    if analysis_audio.get("analysis_audio_fallback_reason") is not None
                    else None
                )
                profile = audio_service.build_waveform_profile(audio_path)
                detected_segments = audio_service.analyze_silence_candidates(
                    audio_path,
                    threshold_db=config.threshold_db,
                    min_duration_ms=effective_min_duration_ms,
                    require_dialogue_valley=True,
                    profile=profile,
                )
                all_silences = []
                for segment in detected_segments:
                    start = float(segment.get("start", 0.0))
                    end = float(segment.get("end", start))
                    if end <= start:
                        continue
                    result_seg = _frame_range_to_segment(
                        timeline_start_frame + int(round(start * fps)),
                        timeline_start_frame + int(round(end * fps)),
                        timeline_start_frame,
                        fps,
                    )
                    if segment.get("kind"):
                        result_seg["kind"] = segment.get("kind")
                    if segment.get("boundary_source"):
                        result_seg["boundary_source"] = segment.get("boundary_source")
                    result_seg["refine_confidence"] = segment.get("refine_confidence")
                    result_seg["refine_reason"] = segment.get("refine_reason")
                    result_seg["used_fallback_seed"] = bool(segment.get("used_fallback_seed", False))
                    all_silences.append(result_seg)
            else:
                analysis_audio_fallback_reason = (
                    str(analysis_audio.get("analysis_audio_fallback_reason") or "master_mix_render_unavailable")
                )
                all_silences = await _analyze_silence_per_clip_fallback(
                    clips,
                    config=config,
                    fps=fps,
                    timeline_start_frame=timeline_start_frame,
                    effective_min_duration_ms=effective_min_duration_ms,
                )
                analysis_audio_source = "per_clip_best_track"
        else:
            all_silences = await _analyze_silence_per_clip_fallback(
                clips,
                config=config,
                fps=fps,
                timeline_start_frame=timeline_start_frame,
                effective_min_duration_ms=effective_min_duration_ms,
            )
            analysis_audio_source = "per_clip_best_track"
            analysis_audio_fallback_reason = "master_mix_disabled"

        total_silence = sum(s["duration"] for s in all_silences)

        await manager.send_progress("silence", 1.0, f"Found {len(all_silences)} silent segments")

        return {
            "segments": all_silences,
            "total_silence_seconds": total_silence,
            "segment_count": len(all_silences),
            "effective_min_duration_ms": effective_min_duration_ms,
            "analysis_audio_source": analysis_audio_source,
            "analysis_audio_fallback_reason": analysis_audio_fallback_reason,
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

        await manager.send_progress("silence_apply", 0.3, "Rendering precision timeline audio...")
        refinement = cut_refiner_service.refine_cut_candidates(
            [
                {
                    "start": float(silence.start),
                    "end": float(silence.end),
                    "duration": float(silence.duration),
                    "selected": bool(silence.selected),
                    "kind": silence.kind,
                    "start_frame": silence.start_frame,
                    "end_frame": silence.end_frame,
                    "boundary_source": getattr(silence, "boundary_source", None),
                    "padding_ms": request.padding_ms,
                }
                for silence in selected_silences
            ],
            name_prefix="finlaycut_silence_apply",
            default_silence_padding_ms=request.padding_ms,
        )
        if refinement.get("error"):
            return {"error": refinement["error"]}

        cut_frame_ranges = list(refinement["cut_frame_ranges"])
        kept_segments = list(refinement["kept_segments"])
        applied_cut_segments = list(refinement["applied_cut_segments"])

        if not cut_frame_ranges:
            return {"error": "Selected pauses no longer have removable dead air after waveform refinement"}

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
            "applied_cut_segments": applied_cut_segments,
            "message": f"Removed {len(cut_frame_ranges)} silence sections ({total_removed:.1f}s) on the current timeline. Backup: {backup_name}"
        }
    except Exception as e:
        await manager.send_error("silence_apply", str(e))
        return {"error": str(e)}
