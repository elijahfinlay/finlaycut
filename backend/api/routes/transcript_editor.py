"""Transcript-based video editing: see the text, delete words, cut the video."""

import logging
from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Literal, Optional
from uuid import uuid4
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.cut_refiner_service import cut_refiner_service
from services.diarization_service import diarization_service
from services.clip_search_service import clip_search_service
from utils.audio_utils import extract_audio, extract_audio_segment, assemble_timeline_audio
from services.audio_state import set_last_transcription_audio
from utils.operation_queue import heavy_operation
from utils.speaker_analysis import build_speaker_summaries, compute_speaker_loudness
from utils.timeline_audio import prepare_timeline_analysis_audio
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.transcript_editor")

router = APIRouter()


class TranscribeRequest(BaseModel):
    language: Optional[str] = None
    in_seconds: Optional[float] = None
    out_seconds: Optional[float] = None
    detect_speakers: bool = False
    filler_words: Optional[list[str]] = None


class KeptSegment(BaseModel):
    start: float
    end: float
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    source_start_frame: Optional[int] = None
    source_end_frame: Optional[int] = None
    file_path: Optional[str] = None
    clip_item_id: Optional[str] = None
    kind: Optional[str] = None
    label: Optional[str] = None
    boundary_source: Optional[str] = None
    prev_word_end: Optional[float] = None
    next_word_start: Optional[float] = None
    prev_word_end_frame: Optional[int] = None
    next_word_start_frame: Optional[int] = None
    padding_ms: Optional[int] = None


class TranscriptContext(BaseModel):
    clip_item_id: Optional[str] = None
    file_path: Optional[str] = None
    clip_start_frame: Optional[int] = None
    clip_end_frame: Optional[int] = None
    source_start_frame: Optional[int] = None
    source_end_frame: Optional[int] = None


class TranscriptSession(BaseModel):
    session_id: str
    timeline_name: Optional[str] = None
    timeline_signature: str
    last_synced_at: str
    sync_state: Literal["fresh", "stale", "reconcilable", "requires_retranscribe"]
    stale_reason: Optional[Literal["external_timeline_change", "signature_mismatch", "ambiguous_sync", "content_changed"]] = None
    origin: Literal["transcribe", "transcript_apply", "reconcile"]


class TranscriptWordPayload(BaseModel):
    word: str
    start: float
    end: float
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    source_start_frame: Optional[int] = None
    source_end_frame: Optional[int] = None
    file_path: Optional[str] = None
    clip_item_id: Optional[str] = None
    kind: Optional[str] = None
    probability: Optional[float] = None
    action: Optional[str] = None
    selected: Optional[bool] = None
    speaker: Optional[str] = None
    speaker_index: Optional[int] = None


class ApplyRequest(BaseModel):
    cut_segments: Optional[list[KeptSegment]] = None
    kept_segments: Optional[list[KeptSegment]] = None
    muted_segments: Optional[list[KeptSegment]] = None
    transcript_context: Optional[TranscriptContext] = None
    transcript_session: Optional[TranscriptSession] = None


class ReconcileRequest(BaseModel):
    words: list[TranscriptWordPayload]
    transcript_context: Optional[TranscriptContext] = None
    transcript_session: Optional[TranscriptSession] = None


class JumpWordRequest(BaseModel):
    word: KeptSegment
    transcript_context: Optional[TranscriptContext] = None


def _match_clip_segment_for_range(
    clip_segments_sorted: list[dict],
    word_start_sec: float,
    word_end_sec: float,
    fps: float,
) -> dict | None:
    midpoint = (word_start_sec + word_end_sec) / 2.0
    for seg in clip_segments_sorted:
        if seg["timeline_start_seconds"] - (1.0 / fps) <= midpoint < seg["timeline_end_seconds"] + (1.0 / fps):
            return seg

    best_match = None
    best_overlap = 0.0
    for seg in clip_segments_sorted:
        overlap_start = max(word_start_sec, seg["timeline_start_seconds"])
        overlap_end = min(word_end_sec, seg["timeline_end_seconds"])
        overlap = max(0.0, overlap_end - overlap_start)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = seg
    return best_match


def _build_clip_segments_from_contexts(
    clip_contexts: list[dict],
    *,
    range_in: float | None = None,
    range_out: float | None = None,
) -> list[dict]:
    segments: list[dict] = []
    for clip_context in clip_contexts:
        clip_start_frame = int(clip_context["clip_start_frame"])
        clip_end_frame = int(clip_context["clip_end_frame"])
        clip_timeline_start_sec = float(clip_context["clip_timeline_offset"])
        clip_duration_sec = float(clip_context["clip_duration_seconds"])
        clip_timeline_end_sec = clip_timeline_start_sec + clip_duration_sec

        if range_in is not None and range_out is not None:
            if clip_timeline_end_sec <= range_in or clip_timeline_start_sec >= range_out:
                continue

        segments.append({
            "timeline_start_seconds": clip_timeline_start_sec,
            "timeline_end_seconds": clip_timeline_end_sec,
            "duration_seconds": clip_duration_sec,
            "timeline_start_frame": clip_start_frame,
            "timeline_end_frame": clip_end_frame,
            "source_start_frame": int(clip_context["source_start_frame"]) if clip_context.get("source_start_frame") is not None else None,
            "source_end_frame": int(clip_context["source_end_frame"]) if clip_context.get("source_end_frame") is not None else None,
            "file_path": str(clip_context["file_path"]) if clip_context.get("file_path") is not None else None,
            "clip_item_id": clip_context.get("clip_item_id"),
            "source_start_seconds": clip_context.get("source_start_seconds"),
            "source_end_seconds": clip_context.get("source_end_seconds"),
        })
    return segments


def _annotate_filler_detections(
    detections: list[dict],
    audio_offset: float,
    timeline_start_frame: int,
    fps: float,
    clip_segments_sorted: list[dict],
) -> list[dict]:
    annotated: list[dict] = []
    for detection in detections:
        try:
            local_start = float(detection.get("start", 0.0))
            local_end = float(detection.get("end", local_start))
        except Exception:
            continue
        if local_end <= local_start:
            continue

        timeline_start = local_start + float(audio_offset)
        timeline_end = local_end + float(audio_offset)
        updated = dict(detection)
        updated["start"] = round(timeline_start, 4)
        updated["end"] = round(timeline_end, 4)
        updated["start_frame"] = int(timeline_start_frame) + int(round(timeline_start * float(fps)))
        updated["end_frame"] = int(timeline_start_frame) + int(round(timeline_end * float(fps)))

        matched_segment = _match_clip_segment_for_range(
            clip_segments_sorted,
            timeline_start,
            timeline_end,
            fps,
        )
        if matched_segment is not None:
            seg_start_frame = int(matched_segment["timeline_start_frame"])
            seg_end_frame = int(matched_segment["timeline_end_frame"])
            detection_start_frame = max(seg_start_frame, min(seg_end_frame, int(updated["start_frame"])))
            detection_end_frame = max(seg_start_frame, min(seg_end_frame, int(updated["end_frame"])))
            updated["start_frame"] = detection_start_frame
            updated["end_frame"] = detection_end_frame

            source_start_frame = matched_segment.get("source_start_frame")
            if source_start_frame is not None:
                updated["source_start_frame"] = int(source_start_frame) + max(0, detection_start_frame - seg_start_frame)
                updated["source_end_frame"] = int(source_start_frame) + max(0, detection_end_frame - seg_start_frame)
            if matched_segment.get("file_path"):
                updated["file_path"] = matched_segment.get("file_path")
            if matched_segment.get("clip_item_id"):
                updated["clip_item_id"] = matched_segment.get("clip_item_id")

        annotated.append(updated)

    return annotated


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


def _build_transcript_session(
    signature_info: dict,
    origin: Literal["transcribe", "transcript_apply", "reconcile"],
    sync_state: Literal["fresh", "stale", "reconcilable", "requires_retranscribe"] = "fresh",
    stale_reason: Optional[Literal["external_timeline_change", "signature_mismatch", "ambiguous_sync", "content_changed"]] = None,
) -> dict:
    return {
        "session_id": str(uuid4()),
        "timeline_name": signature_info.get("timeline_name"),
        "timeline_signature": str(signature_info.get("timeline_signature") or ""),
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "sync_state": sync_state,
        "stale_reason": stale_reason,
        "origin": origin,
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
    bound_signature_info = resolve_service.get_timeline_signature()

    # Determine range to transcribe
    range_in = request.in_seconds
    range_out = request.out_seconds
    is_range = range_in is not None and range_out is not None and range_out > range_in
    range_label = ""
    if is_range:
        range_label = f" (range {range_in:.1f}s - {range_out:.1f}s)"
        logger.info("Transcribing timeline range: %.1fs - %.1fs", range_in, range_out)

    clip_selection = resolve_service.get_timeline_audio_clip_contexts()
    clip_contexts = clip_selection["contexts"]
    if not clip_selection["has_any_items"]:
        return {"error": "No clips found in timeline. Make sure your timeline has at least one clip."}
    clip_segments = _build_clip_segments_from_contexts(
        clip_contexts,
        range_in=range_in if is_range else None,
        range_out=range_out if is_range else None,
    )
    rendered_timeline_audio = None
    analysis_audio_source: str | None = None
    analysis_audio_fallback_reason: str | None = None

    logger.info(
        "Transcribing timeline%s: %d usable clips from %s track %s, %.1fs total duration",
        range_label,
        len(clip_contexts),
        clip_selection["selected_media_type"],
        clip_selection["selected_track_index"],
        timeline_duration_seconds,
    )

    try:
        cached_audio_duration = timeline_duration_seconds
        if bool(getattr(settings, "enable_master_mix_analysis_default", True)):
            render_mark_in = None
            render_mark_out = None
            if is_range:
                render_mark_in = timeline_start_frame + int(round(float(range_in) * fps))
                render_mark_out = timeline_start_frame + int(round(float(range_out) * fps)) - 1
            analysis_audio = prepare_timeline_analysis_audio(
                name_prefix="finlaycut_transcript",
                mark_in_frame=render_mark_in,
                mark_out_frame=render_mark_out,
                fallback_restore_page="edit",
                allow_track_fallback=True,
                reuse_cached_master_mix=False,
            )
            if not analysis_audio.get("audio_path"):
                return {"error": "Could not prepare timeline audio for transcription."}

            audio_path = str(analysis_audio["audio_path"])
            analysis_audio_source = str(analysis_audio.get("analysis_audio_source") or "rendered_master_mix")
            analysis_audio_fallback_reason = (
                str(analysis_audio.get("analysis_audio_fallback_reason"))
                if analysis_audio.get("analysis_audio_fallback_reason") is not None
                else None
            )
            rendered_timeline_audio = audio_path if analysis_audio_source == "rendered_master_mix" else None

            if analysis_audio_source in {"cached_master_mix", "rendered_master_mix"}:
                await manager.send_progress("transcript_editor", 0.10, "Using rendered timeline mix...")
            else:
                await manager.send_progress("transcript_editor", 0.10, "Using best-track fallback audio...")

            audio_offset = 0.0
            cached_audio_duration = timeline_duration_seconds
            if is_range:
                if analysis_audio_source == "assembled_best_track":
                    try:
                        audio_path = extract_audio_segment(audio_path, range_in, range_out)
                    except Exception as e:
                        logger.warning("Range trim failed for assembled transcription audio (%s)", e)
                    else:
                        audio_offset = float(range_in)
                        cached_audio_duration = max(0.0, float(range_out - range_in))
                else:
                    audio_offset = float(range_in)
                    cached_audio_duration = max(0.0, float(range_out - range_in))
        else:
            if not clip_contexts:
                logger.warning("Transcript extraction found no usable clip sources: %s", clip_selection["attempts"])
                render_mark_in = None
                render_mark_out = None
                if is_range:
                    render_mark_in = timeline_start_frame + int(round(float(range_in) * fps))
                    render_mark_out = timeline_start_frame + int(round(float(range_out) * fps)) - 1
                rendered_timeline_audio = resolve_service.render_current_timeline_audio(
                    "finlaycut_transcript",
                    mark_in_frame=render_mark_in,
                    mark_out_frame=render_mark_out,
                    fallback_restore_page="edit",
                )
                if not rendered_timeline_audio:
                    return {"error": "Could not extract audio from any timeline clips."}
                logger.info("Using rendered timeline audio fallback for transcript extraction")
                await manager.send_progress("transcript_editor", 0.10, "Using rendered timeline audio...")
                audio_path = rendered_timeline_audio
                audio_offset = 0.0
                cached_audio_duration = timeline_duration_seconds
                analysis_audio_source = "rendered_master_mix"
                analysis_audio_fallback_reason = None
            else:
                extraction_segments: list[dict] = []
                await manager.send_progress(
                    "transcript_editor",
                    0.02,
                    f"Extracting audio from {len(clip_contexts)} clip(s)...{range_label}",
                )

                # Extract audio for each clip's source range
                audio_cache: dict[str, str] = {}  # source file -> extracted WAV path

                for i, clip_context in enumerate(clip_contexts):
                    file_path = str(clip_context["file_path"])
                    clip_timeline_start_sec = float(clip_context["clip_timeline_offset"])
                    clip_duration_sec = float(clip_context["clip_duration_seconds"])
                    clip_timeline_end_sec = clip_timeline_start_sec + clip_duration_sec

                    # Skip clips entirely outside the in/out range
                    if is_range:
                        if clip_timeline_end_sec <= range_in or clip_timeline_start_sec >= range_out:
                            continue

                    pct = 0.02 + (i / len(clip_contexts)) * 0.08
                    await manager.send_progress(
                        "transcript_editor",
                        pct,
                        f"Extracting audio ({i + 1}/{len(clip_contexts)} clips)...",
                    )

                    # Extract full audio from source file (cached per unique file)
                    if file_path not in audio_cache:
                        audio_cache[file_path] = extract_audio(file_path)
                    full_audio = audio_cache[file_path]

                    # Extract just the source range visible in the timeline
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
                        # No source range — cap to clip duration to avoid using full source file
                        try:
                            clip_audio = extract_audio_segment(full_audio, 0, clip_duration_sec)
                        except Exception:
                            clip_audio = full_audio

                    extraction_segments.append({
                        "audio_path": clip_audio,
                        "timeline_start_seconds": clip_timeline_start_sec,
                        "duration_seconds": clip_duration_sec,
                    })

                if not extraction_segments:
                    return {"error": "No clips found in the selected range." if is_range else "Could not extract audio from any timeline clips."}

                # Assemble into single timeline audio
                await manager.send_progress("transcript_editor", 0.10, "Assembling timeline audio...")

                if len(extraction_segments) == 1 and not is_range:
                    # Single clip full timeline — use its audio directly, track the timeline offset
                    audio_path = extraction_segments[0]["audio_path"]
                    audio_offset = extraction_segments[0]["timeline_start_seconds"]
                    cached_audio_duration = float(extraction_segments[0]["duration_seconds"])
                    analysis_audio_source = "per_clip_best_track"
                    analysis_audio_fallback_reason = "master_mix_disabled"
                else:
                    # Multiple clips or range mode — stitch into one file with correct timeline positions
                    audio_path = assemble_timeline_audio(extraction_segments, timeline_duration_seconds)
                    audio_offset = 0.0  # Assembled audio starts at timeline start
                    cached_audio_duration = timeline_duration_seconds
                    analysis_audio_source = "assembled_best_track"
                    analysis_audio_fallback_reason = "master_mix_disabled"

        # If range mode, trim the assembled audio to just the in/out range
        if is_range and audio_offset == 0.0:
            try:
                if rendered_timeline_audio:
                    audio_offset = range_in  # Rendered fallback already matches the requested span
                    cached_audio_duration = max(0.0, float(range_out - range_in))
                else:
                    trimmed_path = extract_audio_segment(audio_path, range_in, range_out)
                    audio_path = trimmed_path
                    audio_offset = range_in  # Words start at the in-point on the timeline
                    cached_audio_duration = max(0.0, float(range_out - range_in))
            except Exception as e:
                logger.warning("Range trim failed (%s), transcribing full audio", e)

        timeline_signature = str(bound_signature_info.get("timeline_signature") or "") if bound_signature_info else None
        covers_full_timeline = (
            not is_range
            and abs(float(audio_offset)) <= 0.05
            and abs(float(cached_audio_duration) - float(timeline_duration_seconds)) <= max(0.1, 1.0 / fps)
        )
        set_last_transcription_audio(
            audio_path,
            float(cached_audio_duration),
            offset=float(audio_offset),
            timeline_signature=timeline_signature,
            covers_full_timeline=covers_full_timeline,
            audio_source=analysis_audio_source,
        )

        # Transcribe with real progress
        async def progress_fn(pct, msg):
            scaled = 0.12 + pct * 0.83
            await manager.send_progress("transcript_editor", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path,
            language=request.language,
            progress_fn=progress_fn,
        )

        filler_detections: list[dict] | None = None
        if transcript.get("words"):
            try:
                await manager.send_progress("transcript_editor", 0.96, "Detecting filler words...")
                filler_detections = audio_service.analyze_filler_candidates(
                    audio_path,
                    {
                        "words": [dict(word) for word in transcript.get("words", [])],
                    },
                    filler_words=request.filler_words,
                )
            except Exception as e:
                logger.warning("Automatic filler detection failed during transcription: %s", e)
                filler_detections = None

        # Map word timestamps to timeline positions
        words = transcript.get("words", [])
        clip_segments_sorted = sorted(clip_segments, key=lambda seg: seg["timeline_start_seconds"])

        for w in words:
            word_start_sec = float(w["start"]) + audio_offset
            word_end_sec = float(w["end"]) + audio_offset
            w["start_frame"] = timeline_start_frame + int(round(word_start_sec * fps))
            w["end_frame"] = timeline_start_frame + int(round(word_end_sec * fps))
            w["start"] = word_start_sec
            w["end"] = word_end_sec

            matched_segment = _match_clip_segment_for_range(
                clip_segments_sorted,
                word_start_sec,
                word_end_sec,
                fps,
            )
            if matched_segment is not None:
                seg_start_frame = int(matched_segment["timeline_start_frame"])
                seg_end_frame = int(matched_segment["timeline_end_frame"])
                word_start_frame = max(seg_start_frame, min(seg_end_frame, int(w["start_frame"])))
                word_end_frame = max(seg_start_frame, min(seg_end_frame, int(w["end_frame"])))
                source_start_frame = matched_segment.get("source_start_frame")
                if source_start_frame is not None:
                    w["source_start_frame"] = int(source_start_frame) + max(0, word_start_frame - seg_start_frame)
                    w["source_end_frame"] = int(source_start_frame) + max(0, word_end_frame - seg_start_frame)
                if matched_segment.get("file_path"):
                    w["file_path"] = matched_segment.get("file_path")
                if matched_segment.get("clip_item_id"):
                    w["clip_item_id"] = matched_segment.get("clip_item_id")

        # If range mode, filter out any words that leaked outside the range
        if is_range:
            words = [w for w in words if w["start"] >= range_in - 0.1 and w["end"] <= range_out + 0.1]

        transcript_text = " ".join(str(w.get("word", "")).strip() for w in words).strip()
        if not words:
            logger.error(
                "Transcription produced zero words for timeline '%s' (language=%s, range=%s, rendered_fallback=%s)",
                bound_signature_info.get("timeline_name") if bound_signature_info else timeline.GetName(),
                transcript.get("language"),
                is_range,
                bool(rendered_timeline_audio),
            )
            return {
                "error": (
                    "Transcription finished without any word timings. "
                    "This usually means the selected timeline audio had no detectable speech "
                    "or the transcript model returned no usable word tokens."
                ),
                "code": "EMPTY_TRANSCRIPT",
                "text": transcript_text,
                "word_count": 0,
            }

        if filler_detections is not None:
            filler_detections = _annotate_filler_detections(
                filler_detections,
                audio_offset,
                timeline_start_frame,
                fps,
                clip_segments_sorted,
            )
            if is_range:
                filler_detections = [
                    detection for detection in filler_detections
                    if detection["start"] >= range_in - 0.1 and detection["end"] <= range_out + 0.1
                ]

        speaker_segments: list[dict] = []
        speaker_summaries: list[dict] = []
        speaker_method: str | None = None
        speaker_fallback_reason: str | None = None
        speaker_detection_error: str | None = None

        if request.detect_speakers and words:
            try:
                await manager.send_progress("transcript_editor", 0.96, "Detecting speakers...")

                async def speaker_progress_fn(pct: float, msg: str):
                    scaled = 0.96 + min(max(float(pct), 0.0), 1.0) * 0.03
                    await manager.send_progress("transcript_editor", scaled, msg)

                raw_segments, speaker_method, speaker_fallback_reason = await diarization_service.diarize_with_progress(
                    audio_path,
                    progress_fn=speaker_progress_fn,
                )
                loudness = compute_speaker_loudness(audio_path, raw_segments)

                for segment in raw_segments:
                    start = round(float(segment.get("start", 0.0)) + float(audio_offset), 3)
                    end = round(float(segment.get("end", 0.0)) + float(audio_offset), 3)
                    if end <= start:
                        continue
                    speaker_segments.append({
                        **segment,
                        "start": start,
                        "end": end,
                        "duration": round(end - start, 3),
                    })

                words = diarization_service.assign_speakers_to_words(
                    [dict(word) for word in words],
                    speaker_segments,
                )
                speaker_summaries = build_speaker_summaries(speaker_segments, words, loudness)
            except Exception as e:
                speaker_detection_error = str(e)
                logger.warning("Automatic speaker detection failed during transcription: %s", e)

        if len(clip_segments_sorted) == 1:
            only_seg = clip_segments_sorted[0]
            transcript_context = {
                "clip_item_id": only_seg.get("clip_item_id"),
                "file_path": only_seg.get("file_path"),
                "clip_start_frame": only_seg.get("timeline_start_frame"),
                "clip_end_frame": only_seg.get("timeline_end_frame"),
                "source_start_frame": only_seg.get("source_start_frame"),
                "source_end_frame": only_seg.get("source_end_frame"),
            }
        else:
            transcript_context = {
                "clip_item_id": None,
                "file_path": None,
                "clip_start_frame": timeline_start_frame,
                "clip_end_frame": timeline_end_frame,
                "source_start_frame": None,
                "source_end_frame": None,
            }
        transcript_session = (
            _build_transcript_session(bound_signature_info, "transcribe")
            if bound_signature_info is not None
            else None
        )

        payload = {
            "words": words,
            "transcript_context": transcript_context,
            "transcript_session": transcript_session,
            "text": transcript_text,
            "language": transcript.get("language"),
            "word_count": len(words),
            "filler_detections": filler_detections,
            "range": {"in_seconds": range_in, "out_seconds": range_out} if is_range else None,
            "analysis_audio_source": analysis_audio_source,
            "analysis_audio_fallback_reason": analysis_audio_fallback_reason,
            "segments": speaker_segments,
            "speakers": speaker_summaries,
            "speaker_count": len(speaker_summaries),
            "speaker_method": speaker_method,
            "speaker_fallback_reason": speaker_fallback_reason,
            "speaker_detection_error": speaker_detection_error,
        }
        if timeline_signature:
            try:
                persisted = clip_search_service.persist_transcript_payload(
                    timeline_signature,
                    [dict(word) for word in words],
                    replace_existing=not is_range,
                )
                logger.info(
                    "Persisted transcript search text for %d clip bucket(s) on timeline '%s'",
                    persisted,
                    transcript_session.get("timeline_name") if transcript_session else timeline.GetName(),
                )
            except Exception as exc:
                logger.warning("Failed to persist transcript data for clip search: %s", exc)
        logger.info(
            "Transcript complete for '%s': %d words, session_bound=%s, range=%s",
            transcript_session.get("timeline_name") if transcript_session else timeline.GetName(),
            len(words),
            bool(transcript_session),
            is_range,
        )
        await manager.send_progress("transcript_editor", 1.0, "Transcription complete")
        await manager.send_complete("transcript_editor", payload)
        return payload
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


@router.post("/reconcile")
async def reconcile_transcript(request: ReconcileRequest):
    """Try to re-anchor an existing transcript onto the current timeline without re-transcribing."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not request.words:
        return {"error": "No transcript words provided"}

    if request.transcript_session is None:
        return {
            "success": False,
            "code": "REQUIRES_RETRANSCRIBE",
            "reason": "signature_mismatch",
            "message": "This transcript is missing freshness metadata. Re-transcribe before syncing it.",
        }

    signature_info = resolve_service.get_timeline_signature()
    if signature_info is None:
        return {"error": "No active timeline"}

    spoken_words = [
        word.model_dump()
        for word in request.words
        if word.kind not in {"silence", "audio_hesitation"}
        and word.word not in {"[silence]", "[filler]"}
    ]
    if not spoken_words:
        return {
            "success": False,
            "code": "REQUIRES_RETRANSCRIBE",
            "reason": "content_changed",
            "message": "No spoken transcript words are available to sync.",
        }

    result = resolve_service.resolve_transcript_words_on_current_timeline(
        spoken_words,
        request.transcript_context.model_dump() if request.transcript_context else None,
    )
    if not result.get("success"):
        reason = result.get("reason") or "content_changed"
        stale_reason = "ambiguous_sync" if reason == "ambiguous_sync" else "content_changed"
        return {
            "success": False,
            "code": "REQUIRES_RETRANSCRIBE",
            "reason": reason,
            "message": result.get("error", "The current timeline can no longer be safely matched to this transcript."),
            "transcript_session": _build_transcript_session(
                signature_info,
                "reconcile",
                sync_state="requires_retranscribe",
                stale_reason=stale_reason,
            ),
        }

    payload = {
        "success": True,
        "words": result.get("words", []),
        "transcript_context": result.get("transcript_context"),
        "transcript_session": _build_transcript_session(signature_info, "reconcile"),
        "analysis_invalidated": ["fillers", "silences", "speakers"],
        "sync_result": {
            "mode": "reconciled",
            "coverage_ratio": result.get("coverage_ratio", 0.0),
            "confidence": result.get("confidence", 0.0),
        },
        "message": "Transcript synced to the current timeline. Re-run fillers, silences, and speakers if needed.",
    }
    try:
        clip_search_service.persist_transcript_payload(
            str(signature_info.get("timeline_signature") or ""),
            payload["words"],
            replace_existing=True,
        )
    except Exception as exc:
        logger.warning("Failed to persist reconciled transcript data for clip search: %s", exc)
    return payload


@router.post("/apply")
@heavy_operation("Transcript Edit")
async def apply_transcript_edits(request: ApplyRequest):
    """Apply transcript-based edits to the timeline."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not request.cut_segments and not request.kept_segments and not request.muted_segments:
        return {"error": "No transcript edits selected"}

    if request.transcript_session is None or not request.transcript_session.timeline_signature:
        return {
            "error": "This transcript is missing freshness metadata. Re-transcribe before applying transcript edits.",
            "code": "TRANSCRIPT_SESSION_REQUIRED",
            "requires_retranscribe": True,
            "stale_reason": "signature_mismatch",
        }

    current_signature = resolve_service.get_timeline_signature()
    if current_signature is None:
        return {"error": "No active timeline"}

    if request.transcript_session.timeline_signature != current_signature["timeline_signature"]:
        return {
            "error": "Transcript is out of date with the current timeline.",
            "code": "TRANSCRIPT_STALE",
            "requires_retranscribe": True,
            "current_timeline_signature": current_signature["timeline_signature"],
            "stale_reason": "external_timeline_change",
        }

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
    applied_cut_segments: list[dict]
    has_explicit_cut_segments = request.cut_segments is not None

    if has_explicit_cut_segments:
        raw_cut_candidates = []
        for seg in (request.cut_segments or []):
            frame_range = _resolved_frame_range(seg)
            if frame_range is None:
                continue
            seg_start_frame, seg_end_frame = frame_range
            raw_cut_candidates.append({
                "start": (seg_start_frame - tl_start) / fps,
                "end": (seg_end_frame - tl_start) / fps,
                "start_frame": seg_start_frame,
                "end_frame": seg_end_frame,
                "kind": seg.kind,
                "label": seg.label,
                "boundary_source": seg.boundary_source,
                "prev_word_end": seg.prev_word_end,
                "next_word_start": seg.next_word_start,
                "prev_word_end_frame": seg.prev_word_end_frame,
                "next_word_start_frame": seg.next_word_start_frame,
                "padding_ms": seg.padding_ms,
            })

        refinement = cut_refiner_service.refine_cut_candidates(
            raw_cut_candidates,
            name_prefix="finlaycut_transcript_apply",
            default_silence_padding_ms=int(getattr(settings, "silence_padding_ms", 100)),
        )
        if refinement.get("error"):
            return {"error": refinement["error"]}

        cut_frame_ranges = list(refinement["cut_frame_ranges"])
        kept_segments_for_service = list(refinement["kept_segments"])
        applied_cut_segments = list(refinement["applied_cut_segments"])

        total_cut_frames = sum(end - start for start, end in cut_frame_ranges)
        kept_frame_ranges = [
            (int(seg["start_frame"]), int(seg["end_frame"]))
            for seg in kept_segments_for_service
            if seg.get("start_frame") is not None and seg.get("end_frame") is not None
        ]
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
        legacy_cut_ranges = _subtract_frame_ranges(tl_start, tl_end, kept_frame_ranges)
        refinement = cut_refiner_service.refine_cut_candidates(
            [
                _frame_range_to_segment(start_frame, end_frame, tl_start, fps) | {"kind": "speech"}
                for start_frame, end_frame in legacy_cut_ranges
            ],
            name_prefix="finlaycut_transcript_apply",
            default_silence_padding_ms=int(getattr(settings, "silence_padding_ms", 100)),
        )
        if refinement.get("error"):
            return {"error": refinement["error"]}
        cut_frame_ranges = list(refinement["cut_frame_ranges"])
        kept_segments_for_service = list(refinement["kept_segments"])
        applied_cut_segments = list(refinement["applied_cut_segments"])

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
    next_signature = resolve_service.get_timeline_signature()

    return {
        "success": True,
        "cut_count": cut_count,
        "total_removed_seconds": round(total_cut, 1),
        "muted_sections": len(merged_muted),
        "total_muted_seconds": round(muted_seconds, 1),
        "backup_timeline": backup_name,
        "timeline_name": result.get("timeline_name"),
        "updated_transcript_context": updated_context,
        "transcript_session": _build_transcript_session(next_signature, "transcript_apply") if next_signature is not None else None,
        "applied_cut_segments": applied_cut_segments,
        "message": message
    }
