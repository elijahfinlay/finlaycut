"""Transcript-based video editing: see the text, delete words, cut the video."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from utils.audio_utils import extract_audio
from utils.time_utils import seconds_to_frames
from api.websocket import manager

router = APIRouter()


class TranscribeRequest(BaseModel):
    language: Optional[str] = None


class KeptSegment(BaseModel):
    start: float
    end: float


class ApplyRequest(BaseModel):
    kept_segments: list[KeptSegment]


@router.post("/transcribe")
async def transcribe_timeline(request: TranscribeRequest):
    """Transcribe the current timeline audio with word-level timestamps."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        clips = resolve_service.get_timeline_clips("audio", 1)
    if not clips:
        return {"error": "No clips found in timeline. Make sure your timeline has at least one clip."}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path for the clip."}

    try:
        await manager.send_progress("transcript_editor", 0.05, "Extracting audio...")
        audio_path = extract_audio(file_path)

        await manager.send_progress("transcript_editor", 0.1, "Transcribing (this may take a minute)...")
        transcript = audio_service.transcribe(
            audio_path,
            language=request.language,
        )

        await manager.send_progress("transcript_editor", 1.0, "Transcription complete")

        return {
            "words": transcript.get("words", []),
            "text": transcript.get("text", ""),
            "language": transcript.get("language"),
            "word_count": len(transcript.get("words", []))
        }
    except Exception as e:
        await manager.send_error("transcript_editor", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_transcript_edits(request: ApplyRequest):
    """
    Apply transcript-based edits to the timeline.
    Takes the list of kept segments (the parts the user DIDN'T delete)
    and marks the removed sections on the timeline.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not request.kept_segments:
        return {"error": "No segments to keep. You deleted everything!"}

    # Create backup first
    await manager.send_progress("transcript_apply", 0.1, "Creating backup...")
    backup = resolve_service.duplicate_timeline()
    if backup is None:
        return {"error": "Failed to create backup timeline"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    start_frame = timeline.GetStartFrame()

    await manager.send_progress("transcript_apply", 0.3, "Calculating cuts...")

    # Figure out the GAPS between kept segments — those are the parts to cut
    cuts = []
    sorted_segments = sorted(request.kept_segments, key=lambda s: s.start)

    for i in range(len(sorted_segments) - 1):
        gap_start = sorted_segments[i].end
        gap_end = sorted_segments[i + 1].start
        if gap_end - gap_start > 0.05:  # Only cut gaps longer than 50ms
            cuts.append({
                "start": gap_start,
                "end": gap_end,
                "duration": gap_end - gap_start
            })

    await manager.send_progress("transcript_apply", 0.5, f"Marking {len(cuts)} cuts...")

    # Mark the cuts on the timeline with red markers
    for i, cut in enumerate(cuts):
        frame_id = seconds_to_frames(cut["start"], fps) + start_frame
        duration_frames = max(1, seconds_to_frames(cut["duration"], fps))
        timeline.AddMarker(
            frame_id, "Red",
            f"Cut {i + 1}",
            f"Remove {cut['duration']:.1f}s",
            duration_frames
        )

    total_cut = sum(c["duration"] for c in cuts)

    await manager.send_progress("transcript_apply", 1.0, "Done")
    await manager.send_complete("transcript_apply", {
        "cut_count": len(cuts),
        "total_removed": total_cut
    })

    return {
        "success": True,
        "cut_count": len(cuts),
        "total_removed_seconds": round(total_cut, 1),
        "backup_timeline": backup.GetName() if backup else None,
        "message": f"Marked {len(cuts)} cuts on timeline ({total_cut:.1f}s removed). Backup saved."
    }
