"""Transcript-based video editing: see the text, delete words, cut the video."""

import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio, extract_audio_segment, assemble_timeline_audio
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.transcript_editor")

router = APIRouter()


class TranscribeRequest(BaseModel):
    language: Optional[str] = None
    in_seconds: Optional[float] = None
    out_seconds: Optional[float] = None


class KeptSegment(BaseModel):
    start: float
    end: float
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    source_start_frame: Optional[int] = None
    source_end_frame: Optional[int] = None


class TranscriptContext(BaseModel):
    clip_item_id: Optional[str] = None
    file_path: Optional[str] = None
    clip_start_frame: Optional[int] = None
    clip_end_frame: Optional[int] = None
    source_start_frame: Optional[int] = None
    source_end_frame: Optional[int] = None


class ApplyRequest(BaseModel):
    cut_segments: Optional[list[KeptSegment]] = None
    kept_segments: Optional[list[KeptSegment]] = None
    muted_segments: Optional[list[KeptSegment]] = None
    transcript_context: Optional[TranscriptContext] = None


class JumpWordRequest(BaseModel):
    word: KeptSegment
    transcript_context: Optional[TranscriptContext] = None


def _merge_frame_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    valid = [(int(start), int(end)) for start, end in ranges if int(end) > int(start)]
    if not valid:
        return []

    valid.sort(key=lambda r: r[0])
    merged: list[tuple[int, int]] = [valid[0]]
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
    }


@router.post("/transcribe")
@heavy_operation("Transcription")
async def transcribe_timeline(request: TranscribeRequest):
    """Transcribe the timeline audio with word-level timestamps.
    If in_seconds/out_seconds are provided, only transcribe that range.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve. Make sure Resolve is running."}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_end_frame = int(timeline.GetEndFrame())
    timeline_duration_seconds = (timeline_end_frame - timeline_start_frame) / fps

    # Determine range to transcribe
    range_in = request.in_seconds
    range_out = request.out_seconds
    is_range = range_in is not None and range_out is not None and range_out > range_in
    range_label = ""
    if is_range:
        range_label = f" (range {range_in:.1f}s - {range_out:.1f}s)"
        logger.info("Transcribing timeline range: %.1fs - %.1fs", range_in, range_out)

    # Get ALL clips — prefer audio track 1, fall back to video track 1
    clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        return {"error": "No clips found in timeline. Make sure your timeline has at least one clip."}

    logger.info("Transcribing timeline%s: %d clips, %.1fs total duration", range_label, len(clips), timeline_duration_seconds)

    try:
        await manager.send_progress("transcript_editor", 0.02, f"Extracting audio from {len(clips)} clip(s)...{range_label}")

        # Extract audio for each clip's source range
        audio_cache: dict[str, str] = {}  # source file -> extracted WAV path
        clip_segments: list[dict] = []

        for i, clip in enumerate(clips):
            file_path = resolve_service.get_clip_file_path(clip)
            if not file_path:
                logger.warning("Clip %d has no file path, skipping", i)
                continue

            clip_start_frame = int(clip.GetStart())
            clip_end_frame = int(clip.GetEnd())
            clip_timeline_start_sec = (clip_start_frame - timeline_start_frame) / fps
            clip_timeline_end_sec = (clip_end_frame - timeline_start_frame) / fps
            clip_duration_sec = (clip_end_frame - clip_start_frame) / fps

            # Skip clips entirely outside the in/out range
            if is_range:
                if clip_timeline_end_sec <= range_in or clip_timeline_start_sec >= range_out:
                    continue

            pct = 0.02 + (i / len(clips)) * 0.08
            await manager.send_progress("transcript_editor", pct,
                f"Extracting audio ({i + 1}/{len(clips)} clips)...")

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
                    clip_audio = extract_audio_segment(full_audio, src_start, src_end)
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
            return {"error": "No clips found in the selected range." if is_range else "Could not extract audio from any timeline clips."}

        # Assemble into single timeline audio
        await manager.send_progress("transcript_editor", 0.10, "Assembling timeline audio...")

        if len(clip_segments) == 1 and not is_range:
            # Single clip full timeline — use its audio directly, track the timeline offset
            audio_path = clip_segments[0]["audio_path"]
            audio_offset = clip_segments[0]["timeline_start_seconds"]
        else:
            # Multiple clips or range mode — stitch into one file with correct timeline positions
            audio_path = assemble_timeline_audio(clip_segments, timeline_duration_seconds)
            audio_offset = 0.0  # Assembled audio starts at timeline start

        # If range mode, trim the assembled audio to just the in/out range
        if is_range and audio_offset == 0.0:
            try:
                trimmed_path = extract_audio_segment(audio_path, range_in, range_out)
                audio_path = trimmed_path
                audio_offset = range_in  # Words start at the in-point on the timeline
            except Exception as e:
                logger.warning("Range trim failed (%s), transcribing full audio", e)

        # Transcribe with real progress
        async def progress_fn(pct, msg):
            scaled = 0.12 + pct * 0.83
            await manager.send_progress("transcript_editor", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path,
            language=request.language,
            progress_fn=progress_fn,
        )

        # Map word timestamps to timeline positions
        words = transcript.get("words", [])
        for w in words:
            word_start_sec = float(w["start"]) + audio_offset
            word_end_sec = float(w["end"]) + audio_offset
            w["start_frame"] = timeline_start_frame + int(round(word_start_sec * fps))
            w["end_frame"] = timeline_start_frame + int(round(word_end_sec * fps))
            w["start"] = word_start_sec
            w["end"] = word_end_sec

        # If range mode, filter out any words that leaked outside the range
        if is_range:
            words = [w for w in words if w["start"] >= range_in - 0.1 and w["end"] <= range_out + 0.1]

        transcript_context = {
            "clip_item_id": None,
            "file_path": None,
            "clip_start_frame": timeline_start_frame,
            "clip_end_frame": timeline_end_frame,
            "source_start_frame": None,
            "source_end_frame": None,
        }

        await manager.send_progress("transcript_editor", 1.0, "Transcription complete")

        return {
            "words": words,
            "transcript_context": transcript_context,
            "text": " ".join(w["word"] for w in words),
            "language": transcript.get("language"),
            "word_count": len(words),
            "range": {"in_seconds": range_in, "out_seconds": range_out} if is_range else None,
        }
    except Exception as e:
        await manager.send_error("transcript_editor", str(e))
        return {"error": str(e)}


@router.post("/jump-word")
async def jump_to_transcript_word(request: JumpWordRequest):
    """Jump to a transcript word, resolving its current timeline position if the clip moved."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    resolved = resolve_service.resolve_transcript_word_timeline_frame(
        request.word.model_dump(),
        request.transcript_context.model_dump() if request.transcript_context else None,
    )
    if not resolved.get("success"):
        return {"error": resolved.get("error", "Could not resolve transcript word position")}

    success = resolve_service.set_playhead_frame(int(resolved["start_frame"]))
    return {
        "success": success,
        "frame": int(resolved["start_frame"]),
        "end_frame": int(resolved.get("end_frame", resolved["start_frame"])),
        "strategy": resolved.get("strategy"),
        "clip_item_id": resolved.get("clip_item_id"),
    }


@router.post("/apply")
@heavy_operation("Transcript Edit")
async def apply_transcript_edits(request: ApplyRequest):
    """Apply transcript-based edits to the timeline."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not request.cut_segments and not request.kept_segments and not request.muted_segments:
        return {"error": "No transcript edits selected"}

    await manager.send_progress("transcript_editor", 0.1, "Creating backup...")
    backup = resolve_service.duplicate_timeline()
    backup_name = "none"
    if backup is None:
        return {"error": "Could not create backup timeline. In-place transcript edits require a backup; please save and retry."}
    try:
        backup_name = backup.GetName()
    except Exception:
        backup_name = "backup"

    await manager.send_progress("transcript_editor", 0.3, "Calculating cuts...")

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    tl_start = int(timeline.GetStartFrame())
    tl_end = int(timeline.GetEndFrame())
    transcript_context = request.transcript_context.model_dump() if request.transcript_context else None

    def _resolve_segment_frames(seg: KeptSegment) -> tuple[Optional[int], Optional[int]]:
        if transcript_context and (seg.source_start_frame is not None or seg.start_frame is not None):
            resolved = resolve_service.resolve_transcript_word_timeline_frame(
                seg.model_dump(),
                transcript_context,
            )
            if resolved.get("success"):
                return int(resolved["start_frame"]), int(resolved.get("end_frame", resolved["start_frame"]))
        start_frame = int(seg.start_frame) if seg.start_frame is not None else None
        end_frame = int(seg.end_frame) if seg.end_frame is not None else None
        return start_frame, end_frame

    def _seg_start_seconds(seg: KeptSegment) -> float:
        resolved_start_frame, _ = _resolve_segment_frames(seg)
        if resolved_start_frame is not None:
            return (float(resolved_start_frame) - tl_start) / fps
        return float(seg.start)

    def _seg_end_seconds(seg: KeptSegment) -> float:
        _, resolved_end_frame = _resolve_segment_frames(seg)
        if resolved_end_frame is not None:
            return (float(resolved_end_frame) - tl_start) / fps
        return float(seg.end)

    def _resolved_frame_range(seg: KeptSegment) -> Optional[tuple[int, int]]:
        seg_start_frame, seg_end_frame = _resolve_segment_frames(seg)
        if seg_start_frame is None or seg_end_frame is None:
            seg_start_frame = tl_start + int(round(float(seg.start) * fps))
            seg_end_frame = tl_start + int(round(float(seg.end) * fps))
        seg_start_frame = max(tl_start, min(int(seg_start_frame), int(tl_end)))
        seg_end_frame = max(tl_start, min(int(seg_end_frame), int(tl_end)))
        if seg_end_frame <= seg_start_frame:
            return None
        return int(seg_start_frame), int(seg_end_frame)

    cut_frame_ranges: list[tuple[int, int]]
    kept_segments_for_service: list[dict]
    has_explicit_cut_segments = request.cut_segments is not None

    if has_explicit_cut_segments:
        raw_cut_ranges = [
            frame_range
            for seg in (request.cut_segments or [])
            for frame_range in [_resolved_frame_range(seg)]
            if frame_range is not None
        ]
        cut_frame_ranges = _merge_frame_ranges(raw_cut_ranges)
        kept_frame_ranges = _subtract_frame_ranges(tl_start, tl_end, cut_frame_ranges)
        kept_segments_for_service = [
            _frame_range_to_segment(start_frame, end_frame, tl_start, fps)
            for start_frame, end_frame in kept_frame_ranges
        ]

        total_cut_frames = sum(end - start for start, end in cut_frame_ranges)
        total_kept_frames = sum(end - start for start, end in kept_frame_ranges)
        total_timeline_frames = tl_end - tl_start
        logger.info(
            "Cut segments: %d raw → %d merged ranges, cut_frames=%d, kept_frames=%d, timeline_frames=%d (%.1f%% kept)",
            len(request.cut_segments or []),
            len(cut_frame_ranges),
            total_cut_frames,
            total_kept_frames,
            total_timeline_frames,
            (total_kept_frames / total_timeline_frames * 100) if total_timeline_frames > 0 else 0,
        )
    else:
        legacy_segments = sorted(request.kept_segments or [], key=_seg_start_seconds)
        kept_segments_for_service = []
        for seg in legacy_segments:
            frame_range = _resolved_frame_range(seg)
            if frame_range is None:
                continue
            seg_start_frame, seg_end_frame = frame_range
            seg_start = (seg_start_frame - tl_start) / fps
            seg_end = (seg_end_frame - tl_start) / fps

            if kept_segments_for_service and seg_start - kept_segments_for_service[-1]["end"] < 0.05:
                kept_segments_for_service[-1]["end"] = max(kept_segments_for_service[-1]["end"], seg_end)
                kept_segments_for_service[-1]["end_frame"] = max(
                    int(kept_segments_for_service[-1].get("end_frame", seg_end_frame)),
                    int(seg_end_frame),
                )
            else:
                kept_segments_for_service.append({
                    "start": seg_start,
                    "end": seg_end,
                    "start_frame": seg_start_frame,
                    "end_frame": seg_end_frame,
                })

        kept_frame_ranges = _merge_frame_ranges([
            (int(seg["start_frame"]), int(seg["end_frame"]))
            for seg in kept_segments_for_service
            if seg.get("start_frame") is not None and seg.get("end_frame") is not None
        ])
        cut_frame_ranges = _subtract_frame_ranges(tl_start, tl_end, kept_frame_ranges)

    if not kept_segments_for_service:
        return {"error": "Nothing would remain after applying those transcript edits"}

    muted_input = sorted(request.muted_segments or [], key=_seg_start_seconds)
    resolved_muted = []
    for seg in muted_input:
        frame_range = _resolved_frame_range(seg)
        if frame_range is None:
            continue
        resolved_muted.append(_frame_range_to_segment(frame_range[0], frame_range[1], tl_start, fps))

    clipped_muted = []
    keep_idx = 0
    for seg in resolved_muted:
        mute_start = float(seg["start"])
        mute_end = float(seg["end"])
        if mute_end <= mute_start:
            continue

        while keep_idx < len(kept_segments_for_service) and kept_segments_for_service[keep_idx]["end"] <= mute_start:
            keep_idx += 1

        probe = keep_idx
        while probe < len(kept_segments_for_service) and kept_segments_for_service[probe]["start"] < mute_end:
            keep = kept_segments_for_service[probe]
            overlap_start = max(mute_start, keep["start"])
            overlap_end = min(mute_end, keep["end"])
            if overlap_end > overlap_start:
                clipped_muted.append({"start": overlap_start, "end": overlap_end})
            probe += 1

    merged_muted = []
    for seg in clipped_muted:
        if merged_muted and seg["start"] - merged_muted[-1]["end"] < 0.02:
            merged_muted[-1]["end"] = max(merged_muted[-1]["end"], seg["end"])
        else:
            merged_muted.append(seg)

    total_cut = sum((end_frame - start_frame) / fps for start_frame, end_frame in cut_frame_ranges)
    cut_count = len(cut_frame_ranges)
    muted_seconds = sum(seg["end"] - seg["start"] for seg in merged_muted)

    if cut_count > 0 or merged_muted:
        await manager.send_progress("transcript_editor", 0.5,
            f"Rebuilding timeline ({cut_count} cuts, {len(merged_muted)} mutes)...")

        result = resolve_service.rebuild_timeline_with_audio_mutes(
            kept_segments=kept_segments_for_service,
            muted_segments=merged_muted,
            in_place=True,
        )

        if not result["success"]:
            return {"error": result.get("error", "Failed to apply edits to timeline")}

        logger.info("Rebuild: %d kept segments, %d muted segments applied", len(kept_segments_for_service), len(merged_muted))

        # Auto-close gaps between clips so user doesn't have to Cmd+G manually
        await manager.send_progress("transcript_editor", 0.85, "Closing gaps...")
        import time as _time
        _time.sleep(0.3)  # Let Resolve settle after rebuild
        resolve_service.select_all_and_close_gaps()
        _time.sleep(0.3)

        # Auto-link video+audio pairs so clips stay linked
        await manager.send_progress("transcript_editor", 0.92, "Linking clips...")
        resolve_service.link_all_clips()
        _time.sleep(0.2)
    else:
        result = {"success": True, "timeline_name": timeline.GetName()}

    # Re-read timeline bounds after rebuild so the frontend can update its context
    updated_context = None
    try:
        updated_tl = resolve_service.get_current_timeline()
        if updated_tl:
            updated_tl_start = int(updated_tl.GetStartFrame())
            updated_tl_end = int(updated_tl.GetEndFrame())
            updated_context = {
                "clip_start_frame": updated_tl_start,
                "clip_end_frame": updated_tl_end,
                "file_path": transcript_context.get("file_path") if transcript_context else None,
                "clip_item_id": None,
                "source_start_frame": None,
                "source_end_frame": None,
            }
    except Exception:
        pass

    await manager.send_progress("transcript_editor", 1.0, "Done")
    await manager.send_complete("transcript_editor", {
        "cut_count": cut_count,
        "total_removed": round(total_cut, 1),
        "muted_sections": len(merged_muted),
        "total_muted": round(muted_seconds, 1),
    })

    from api.routes.undo import _record
    edit_summary = f"Removed {cut_count} sections ({total_cut:.1f}s)"
    if cut_count == 0:
        edit_summary = "Updated transcript edits"
    if muted_seconds > 0:
        edit_summary += f", muted {len(merged_muted)} sections ({muted_seconds:.1f}s)"
    _record("Transcript Edit", backup_name, edit_summary)

    if cut_count > 0:
        message = f"Removed {cut_count} sections ({total_cut:.1f}s) from the current timeline"
    else:
        message = "Applied transcript edits on the current timeline"
    if muted_seconds > 0:
        message += f", muted {len(merged_muted)} sections ({muted_seconds:.1f}s silenced)"
    message += f". Backup: {backup_name}"

    return {
        "success": True,
        "cut_count": cut_count,
        "total_removed_seconds": round(total_cut, 1),
        "muted_sections": len(merged_muted),
        "total_muted_seconds": round(muted_seconds, 1),
        "backup_timeline": backup_name,
        "timeline_name": result.get("timeline_name"),
        "updated_transcript_context": updated_context,
        "message": message
    }
