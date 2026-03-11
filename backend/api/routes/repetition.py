"""Repetition/take detection and removal endpoints."""

import json
import logging
from pathlib import Path
from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from services.cut_refiner_service import cut_refiner_service
from utils.audio_utils import extract_audio, extract_audio_segment
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.repetition")

router = APIRouter()

DUPLICATE_CLIP_MARKER_NAMESPACE = "finlaycut:duplicate-clip-review"
DUPLICATE_CLIP_MARKER_COLOR = "Rose"


class RepetitionDetectRequest(BaseModel):
    transcript: Optional[dict] = None


def _take_to_frame_range(take: dict, timeline_start_frame: int, fps: float) -> tuple[int, int]:
    start_frame = take.get("start_frame")
    end_frame = take.get("end_frame")
    if isinstance(start_frame, (int, float)) and isinstance(end_frame, (int, float)):
        return int(start_frame), int(end_frame)

    start_seconds = float(take.get("start", 0))
    end_seconds = float(take.get("end", 0))
    return (
        int(timeline_start_frame) + int(round(start_seconds * float(fps))),
        int(timeline_start_frame) + int(round(end_seconds * float(fps))),
    )


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
    }


def _coerce_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _build_duplicate_clip_groups(
    records: list[dict],
    timeline_start_frame: int,
    fps: float,
) -> tuple[list[dict], int]:
    grouped: dict[tuple[str, int, int], dict] = {}
    skipped_untrackable = 0

    for record in records:
        clip_start_frame = _coerce_int(record.get("clip_start_frame"))
        clip_end_frame = _coerce_int(record.get("clip_end_frame"))
        source_start_frame = _coerce_int(record.get("source_start_frame"))
        source_end_frame = _coerce_int(record.get("source_end_frame"))

        if (
            clip_start_frame is None
            or clip_end_frame is None
            or clip_end_frame <= clip_start_frame
            or source_start_frame is None
            or source_end_frame is None
            or source_end_frame <= source_start_frame
        ):
            skipped_untrackable += 1
            continue

        media_pool_item_unique_id = str(record.get("media_pool_item_unique_id") or "").strip()
        file_path = str(record.get("file_path") or "").strip()
        source_key = media_pool_item_unique_id or file_path
        if not source_key:
            skipped_untrackable += 1
            continue

        clip_name = str(record.get("clip_name") or "").strip() or "Clip"
        source_label = Path(file_path).name if file_path else f"Media {source_key[:8]}"
        group_key = (source_key, source_start_frame, source_end_frame)
        group = grouped.setdefault(
            group_key,
            {
                "source_key": source_key,
                "source_label": source_label,
                "source_start_frame": source_start_frame,
                "source_end_frame": source_end_frame,
                "instances": [],
            },
        )

        group["instances"].append(
            {
                "item": record.get("item"),
                "clip_name": clip_name,
                "track_index": int(record.get("track_index") or 1),
                "timeline_item_unique_id": record.get("timeline_item_unique_id"),
                "timeline_start_frame": clip_start_frame,
                "timeline_end_frame": clip_end_frame,
                "timeline_start_seconds": (clip_start_frame - int(timeline_start_frame)) / float(fps),
                "timeline_end_seconds": (clip_end_frame - int(timeline_start_frame)) / float(fps),
                "source_start_frame": source_start_frame,
                "source_end_frame": source_end_frame,
            }
        )

    groups = [group for group in grouped.values() if len(group["instances"]) > 1]
    for group in groups:
        group["instances"].sort(
            key=lambda instance: (
                int(instance["timeline_start_frame"]),
                int(instance["track_index"]),
                str(instance.get("timeline_item_unique_id") or ""),
            )
        )
        group["instance_count"] = len(group["instances"])

    groups.sort(
        key=lambda group: (
            int(group["instances"][0]["timeline_start_frame"]),
            int(group["instances"][0]["track_index"]),
            str(group["source_label"]),
        )
    )
    return groups, skipped_untrackable


def _serialize_duplicate_clip_groups(groups: list[dict]) -> list[dict]:
    serialized_groups: list[dict] = []
    for group in groups:
        serialized_groups.append(
            {
                "source_key": group["source_key"],
                "source_label": group["source_label"],
                "source_start_frame": int(group["source_start_frame"]),
                "source_end_frame": int(group["source_end_frame"]),
                "instance_count": int(group.get("instance_count") or len(group.get("instances") or [])),
                "instances": [
                    {
                        "clip_name": str(instance.get("clip_name") or "Clip"),
                        "track_index": int(instance.get("track_index") or 1),
                        "timeline_item_unique_id": instance.get("timeline_item_unique_id"),
                        "timeline_start_frame": int(instance["timeline_start_frame"]),
                        "timeline_end_frame": int(instance["timeline_end_frame"]),
                        "timeline_start_seconds": float(instance["timeline_start_seconds"]),
                        "timeline_end_seconds": float(instance["timeline_end_seconds"]),
                        "source_start_frame": int(instance["source_start_frame"]),
                        "source_end_frame": int(instance["source_end_frame"]),
                    }
                    for instance in group.get("instances", [])
                ],
            }
        )
    return serialized_groups


def _build_duplicate_clip_markers(groups: list[dict]) -> tuple[list[dict], list[dict]]:
    timeline_markers: list[dict] = []
    timeline_item_markers: list[dict] = []

    for group_index, group in enumerate(groups):
        for instance_index, instance in enumerate(group.get("instances", [])):
            clip_start_frame = int(instance["timeline_start_frame"])
            clip_end_frame = int(instance["timeline_end_frame"])
            source_start_frame = int(instance["source_start_frame"])
            source_end_frame = int(instance["source_end_frame"])
            timeline_duration = max(1, clip_end_frame - clip_start_frame)
            source_duration = max(1, source_end_frame - source_start_frame)
            clip_name = str(instance.get("clip_name") or "Clip")
            track_index = int(instance.get("track_index") or 1)

            shared_note = (
                f'Duplicate clip detected: "{clip_name}" on V{track_index}.\n'
                f"Source: {group['source_label']} [{source_start_frame}-{source_end_frame}]\n"
                f"Timeline: {float(instance['timeline_start_seconds']):.2f}s-{float(instance['timeline_end_seconds']):.2f}s"
            )
            marker_payload = {
                "version": 1,
                "feature": "duplicate_clip_review",
                "kind": "duplicate_clip",
                "group_index": int(group_index),
                "instance_index": int(instance_index),
                "source_key": group["source_key"],
                "source_start_frame": source_start_frame,
                "source_end_frame": source_end_frame,
                "track_index": track_index,
                "timeline_item_unique_id": instance.get("timeline_item_unique_id"),
                "clip_name": clip_name,
            }

            timeline_markers.append(
                {
                    "frame_id": clip_start_frame,
                    "duration": timeline_duration,
                    "color": DUPLICATE_CLIP_MARKER_COLOR,
                    "name": f"Duplicate Clip - {clip_name}",
                    "note": shared_note,
                    "custom_data": json.dumps(
                        {**marker_payload, "marker_target": "timeline"},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                }
            )

            item = instance.get("item")
            if item is None:
                continue
            timeline_item_markers.append(
                {
                    "item": item,
                    "frame_id": source_start_frame,
                    "duration": source_duration,
                    "color": DUPLICATE_CLIP_MARKER_COLOR,
                    "name": f"Duplicate Clip - {clip_name}",
                    "note": f"{shared_note}\nReview this duplicate source span on the timeline.",
                    "custom_data": json.dumps(
                        {**marker_payload, "marker_target": "timeline_item"},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                }
            )

    return timeline_markers, timeline_item_markers


@router.post("/detect")
@heavy_operation("Repetition Detection")
async def detect_repetitions(request: RepetitionDetectRequest = RepetitionDetectRequest()):
    """Detect repeated takes in the timeline."""

    # Path A: transcript provided — skip transcription entirely
    if request.transcript is not None:
        try:
            await manager.send_progress("repetition", 0.5, "Detecting repetitions...")
            groups = await ai_service.detect_repetitions(request.transcript)
            await manager.send_progress("repetition", 1.0, f"Found {len(groups)} repeated sections")
            return {"groups": groups, "count": len(groups)}
        except Exception as e:
            await manager.send_error("repetition", str(e))
            return {"error": str(e)}

    # Path B: no transcript — extract audio and transcribe
    clip = resolve_service.get_clip_at_playhead("audio", 1)
    if clip is None:
        clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("repetition", 0.02, "Extracting audio...")

        clip_context = resolve_service.get_clip_audio_context(clip)
        if clip_context is None:
            return {"error": "Could not determine clip timing context"}

        full_audio = extract_audio(file_path)
        audio_path = full_audio
        if clip_context.get("source_start_seconds") is not None and clip_context.get("source_end_seconds") is not None:
            audio_path = extract_audio_segment(
                full_audio,
                clip_context["source_start_seconds"],
                clip_context["source_end_seconds"],
            )

        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.50
            await manager.send_progress("repetition", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, progress_fn=progress_fn,
        )

        await manager.send_progress("repetition", 0.6, "Detecting repetitions...")
        groups = await ai_service.detect_repetitions(transcript)

        # Offset take timestamps to the timeline and derive frame-accurate ranges.
        offset = clip_context.get("clip_timeline_offset", 0)
        fps = float(clip_context.get("fps") or 24.0)
        timeline_start_frame = int(clip_context.get("timeline_start_frame") or 0)
        for group in groups:
            for take in group.get("takes", []):
                if "start" in take:
                    take["start"] += offset
                if "end" in take:
                    take["end"] += offset
                if take.get("start_frame") is None and "start" in take:
                    take["start_frame"] = timeline_start_frame + int(round(float(take["start"]) * fps))
                if take.get("end_frame") is None and "end" in take:
                    take["end_frame"] = timeline_start_frame + int(round(float(take["end"]) * fps))

        await manager.send_progress("repetition", 1.0, f"Found {len(groups)} repeated sections")

        return {
            "groups": groups,
            "count": len(groups)
        }
    except Exception as e:
        await manager.send_error("repetition", str(e))
        return {"error": str(e)}


@router.post("/duplicate-clips/scan")
async def scan_duplicate_clips():
    """Detect duplicate clips on the current timeline and refresh review markers."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    timeline_start_frame = int(timeline.GetStartFrame())
    timeline_name = timeline.GetName() if hasattr(timeline, "GetName") else None

    raw_records = resolve_service._get_timeline_item_records(media_types=("video",))
    duplicate_candidate_records: list[dict] = []
    for record in raw_records:
        item = record.get("item")
        duplicate_candidate_records.append(
            {
                **record,
                "media_pool_item_unique_id": (
                    str(record.get("media_pool_item_unique_id") or "").strip()
                    or resolve_service.get_media_pool_item_unique_id(item)
                ),
                "clip_name": (
                    str(record.get("clip_name") or "").strip()
                    or resolve_service.get_timeline_item_name(item)
                ),
            }
        )

    groups, skipped_untrackable_count = _build_duplicate_clip_groups(
        duplicate_candidate_records,
        timeline_start_frame=timeline_start_frame,
        fps=fps,
    )
    serialized_groups = _serialize_duplicate_clip_groups(groups)

    if not groups:
        clear_result = resolve_service.refresh_managed_markers(
            DUPLICATE_CLIP_MARKER_NAMESPACE,
            clear_item_media_types=("video",),
        )
        if clear_result.get("error"):
            return {"error": clear_result["error"]}

        cleared_count = int(clear_result.get("deleted_count") or 0)
        message = "No duplicate clips detected"
        if timeline_name:
            message = f"{message} on {timeline_name}"
        if cleared_count:
            message = f"{message}. Cleared {cleared_count} duplicate review markers"
        if skipped_untrackable_count:
            message = f"{message} ({skipped_untrackable_count} skipped)"

        return {
            "success": True,
            "groups": [],
            "duplicate_group_count": 0,
            "duplicate_instance_count": 0,
            "timeline_marker_count": 0,
            "clip_marker_count": 0,
            "cleared_count": cleared_count,
            "skipped_untrackable_count": skipped_untrackable_count,
            "message": message,
        }

    timeline_markers, timeline_item_markers = _build_duplicate_clip_markers(groups)
    result = resolve_service.refresh_managed_markers(
        DUPLICATE_CLIP_MARKER_NAMESPACE,
        timeline_markers=timeline_markers,
        timeline_item_markers=timeline_item_markers,
        clear_item_media_types=("video",),
    )
    if result.get("error"):
        return {"error": result["error"]}

    duplicate_instance_count = sum(int(group.get("instance_count") or 0) for group in groups)
    timeline_marker_count = int(result.get("timeline_applied_count") or 0)
    clip_marker_count = int(result.get("timeline_item_applied_count") or 0)
    failed_count = len(result.get("failed_frames") or []) + len(result.get("timeline_item_failed_frames") or [])

    message = (
        f"Flagged {duplicate_instance_count} duplicate clip instance"
        f"{'' if duplicate_instance_count == 1 else 's'} across {len(groups)} duplicate source span"
        f"{'' if len(groups) == 1 else 's'}"
    )
    if timeline_name:
        message = f"{message} on {timeline_name}"
    if skipped_untrackable_count:
        message = f"{message} ({skipped_untrackable_count} skipped)"

    response = {
        "success": True,
        "groups": serialized_groups,
        "duplicate_group_count": len(groups),
        "duplicate_instance_count": duplicate_instance_count,
        "timeline_marker_count": timeline_marker_count,
        "clip_marker_count": clip_marker_count,
        "cleared_count": int(result.get("deleted_count") or 0),
        "skipped_untrackable_count": skipped_untrackable_count,
        "message": message,
    }
    if failed_count:
        response["warning"] = (
            f"Placed {timeline_marker_count + clip_marker_count} of "
            f"{len(timeline_markers) + len(timeline_item_markers)} duplicate review markers. "
            "Some markers could not be added, likely because another marker already occupies that frame."
        )
    return response


@router.post("/apply")
async def apply_repetition_removal(groups: list[dict]):
    """Remove unwanted takes, keeping the best one from each group."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not groups:
        return {"error": "No repetition groups provided"}

    # Create backup (non-blocking — proceed even if backup fails)
    backup = resolve_service.duplicate_timeline()
    backup_name = "none"
    if backup is not None:
        try:
            backup_name = backup.GetName()
        except Exception:
            backup_name = "backup"
    else:
        logger.warning("Could not create backup timeline")

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    tl_start = timeline.GetStartFrame()
    tl_end = timeline.GetEndFrame()
    total_duration = (tl_end - tl_start) / fps

    # For each group, identify the segments to REMOVE (all takes except the best)
    segments_to_remove = []
    for group in groups:
        best_idx = group.get("best_index", 0)
        takes = group.get("takes", [])
        for ti, take in enumerate(takes):
            if ti != best_idx:
                segments_to_remove.append({
                    "start": take.get("start", 0),
                    "end": take.get("end", 0),
                })

    if not segments_to_remove:
        return {"success": True, "message": "Nothing to remove"}

    repetition_candidates = []
    for rem in segments_to_remove:
        start_frame, end_frame = _take_to_frame_range(rem, tl_start, fps)
        repetition_candidates.append({
            **rem,
            "kind": "retake",
            "start_frame": start_frame,
            "end_frame": end_frame,
            "prev_word_end": rem.get("prev_word_end"),
            "prev_word_end_frame": rem.get("prev_word_end_frame"),
            "next_word_start": rem.get("next_word_start"),
            "next_word_start_frame": rem.get("next_word_start_frame"),
        })

    refinement = cut_refiner_service.refine_cut_candidates(
        repetition_candidates,
        name_prefix="finlaycut_repetition_apply",
    )
    if refinement.get("error"):
        return {"error": refinement["error"]}

    cut_frame_ranges = list(refinement["cut_frame_ranges"])
    kept_segments = list(refinement["kept_segments"])
    applied_cut_segments = list(refinement["applied_cut_segments"])
    if not cut_frame_ranges:
        return {"error": "No duplicate take sections remained removable after waveform refinement"}
    if not kept_segments:
        return {"error": "Nothing would remain after removing the selected takes"}

    result = resolve_service.rebuild_timeline_keeping(kept_segments, in_place=True)
    if not result["success"]:
        return {"error": result.get("error", "Failed to apply edits to current timeline")}

    total_removed = sum((end_frame - start_frame) / fps for start_frame, end_frame in cut_frame_ranges)

    from api.routes.undo import _record
    undo_entry = _record(
        "Repetition Removal",
        backup_name,
        f"Removed {len(segments_to_remove)} takes ({total_removed:.1f}s)",
    )

    return {
        "success": True,
        "removed_takes": len(cut_frame_ranges),
        "total_removed_seconds": round(total_removed, 1),
        "backup_timeline": backup_name,
        "timeline_name": result.get("timeline_name"),
        "applied_cut_segments": applied_cut_segments,
        "undo_entry_id": undo_entry["id"],
        "message": f"Removed {len(cut_frame_ranges)} duplicate take sections ({total_removed:.1f}s) on the current timeline. Backup: {backup_name}"
    }
