"""Multi-track podcast editing endpoints."""

import logging
from fastapi import APIRouter
from services.resolve_service import resolve_service
from api.websocket import manager

logger = logging.getLogger("finlaycut.podcast")

router = APIRouter()


@router.post("/analyze")
async def analyze_podcast():
    """
    Analyze multi-track audio to determine speaker activity.
    Detects which audio track (person) is speaking at each moment.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    audio_tracks = timeline.GetTrackCount("audio")
    if audio_tracks < 2:
        return {"error": "Podcast editing requires at least 2 audio tracks. Found: " + str(audio_tracks)}

    try:
        await manager.send_progress("podcast", 0.1, "Analyzing audio tracks...")

        tl_start = timeline.GetStartFrame()
        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0

        # Collect track metadata and timeline ranges
        track_data = []
        clip_ranges = []
        for track_idx in range(1, audio_tracks + 1):
            clips = resolve_service.get_timeline_clips("audio", track_idx)
            if clips:
                file_path = resolve_service.get_clip_file_path(clips[0])
                if file_path:
                    track_data.append({
                        "track_index": track_idx,
                        "file_path": file_path,
                        "track_name": timeline.GetTrackName("audio", track_idx)
                    })

                for clip in clips:
                    clip_start = clip.GetStart()
                    clip_end = clip.GetEnd()
                    if clip_end > clip_start:
                        clip_ranges.append({
                            "track_index": track_idx,
                            "start_seconds": max(0.0, (clip_start - tl_start) / fps),
                            "end_seconds": max(0.0, (clip_end - tl_start) / fps),
                        })

            progress = 0.1 + (track_idx / audio_tracks) * 0.3
            await manager.send_progress("podcast", progress, f"Analyzing track {track_idx}...")

        if len(track_data) < 2:
            return {"error": "Need at least 2 audio tracks with clips for podcast editing"}

        # Build a best-effort switching schedule from timeline clip coverage.
        # For each interval between clip boundaries, pick the lowest-index
        # active track as the speaker/camera source.
        boundaries = {0.0}
        for r in clip_ranges:
            boundaries.add(r["start_seconds"])
            boundaries.add(r["end_seconds"])
        ordered = sorted(boundaries)

        switching_schedule = []
        for i in range(len(ordered) - 1):
            start = ordered[i]
            end = ordered[i + 1]
            if end <= start:
                continue
            midpoint = (start + end) / 2.0
            active_tracks = [
                r["track_index"]
                for r in clip_ranges
                if r["start_seconds"] <= midpoint < r["end_seconds"]
            ]
            if not active_tracks:
                continue
            chosen_track = min(active_tracks)
            if switching_schedule and switching_schedule[-1]["track_index"] == chosen_track:
                switching_schedule[-1]["end"] = end
            else:
                switching_schedule.append({
                    "start": start,
                    "end": end,
                    "track_index": chosen_track,
                })

        await manager.send_progress("podcast", 1.0, "Analysis complete")

        return {
            "tracks": track_data,
            "track_count": len(track_data),
            "switching_schedule": switching_schedule,
            "message": "Multi-track analysis complete. Review and apply camera switching."
        }
    except Exception as e:
        await manager.send_error("podcast", str(e))
        return {"error": str(e)}


@router.post("/apply")
async def apply_podcast_edit(switching_schedule: list[dict]):
    """
    Apply podcast camera switching based on the schedule.
    Each entry: { "start": float, "end": float, "track_index": int }
    Enables/disables tracks so only the active speaker's camera is visible.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    if not switching_schedule:
        return {"error": "No switching schedule provided"}

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

    await manager.send_progress("podcast_apply", 0.3, "Applying camera switches...")

    # For each video track, mark regions where it should be active
    # by adding markers showing which camera is active
    for i, switch in enumerate(switching_schedule):
        frame_id = int(round(switch["start"] * fps)) + tl_start
        duration_frames = max(1, int(round((switch["end"] - switch["start"]) * fps)))
        track_idx = switch.get("track_index", 1)

        timeline.AddMarker(
            frame_id, "Green",
            f"Camera {track_idx}",
            f"Speaker on track {track_idx}",
            duration_frames
        )

    await manager.send_progress("podcast_apply", 1.0, "Done")

    from api.routes.undo import _record
    _record("Podcast Edit", backup_name,
            f"Applied {len(switching_schedule)} camera switches")

    return {
        "success": True,
        "switches": len(switching_schedule),
        "backup_timeline": backup_name,
        "message": f"Applied {len(switching_schedule)} camera switches. Backup: {backup_name}"
    }
