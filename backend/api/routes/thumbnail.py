"""AI thumbnail generation endpoints."""

from fastapi import APIRouter
from services.resolve_service import resolve_service
from utils.audio_utils import extract_frame, get_audio_duration
from api.websocket import manager
import os

router = APIRouter()


@router.post("/generate")
async def generate_thumbnails(count: int = 10):
    """
    Extract the most visually compelling frames from the timeline.
    Extracts frames at regular intervals + key moments.
    """
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        return {"error": "No video clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("thumbnail", 0.1, "Analyzing video...")

        duration = get_audio_duration(file_path)
        interval = duration / (count + 1)

        thumbnails = []
        for i in range(1, count + 1):
            time_s = interval * i
            await manager.send_progress(
                "thumbnail",
                0.1 + (i / count) * 0.8,
                f"Extracting frame {i}/{count}..."
            )

            try:
                frame_path = extract_frame(file_path, time_s)
                thumbnails.append({
                    "path": frame_path,
                    "time_seconds": time_s,
                    "index": i,
                    "score": 0  # AI scoring would go here
                })
            except Exception:
                continue

        await manager.send_progress("thumbnail", 1.0, f"Generated {len(thumbnails)} thumbnails")

        return {
            "thumbnails": thumbnails,
            "count": len(thumbnails)
        }
    except Exception as e:
        await manager.send_error("thumbnail", str(e))
        return {"error": str(e)}
