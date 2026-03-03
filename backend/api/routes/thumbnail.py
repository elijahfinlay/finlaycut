"""AI thumbnail generation endpoints."""

import logging
import os
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from services.resolve_service import resolve_service
from utils.audio_utils import extract_frame, get_audio_duration
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.thumbnail")
router = APIRouter()


@router.post("/generate")
async def generate_thumbnails(count: int = Query(default=10, ge=1, le=50)):
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


@router.get("/serve/{filename}")
async def serve_thumbnail(filename: str):
    """Serve a generated thumbnail image by filename."""
    # Sanitize filename to prevent path traversal
    safe_name = os.path.basename(filename)
    file_path = os.path.join(str(settings.temp_dir), safe_name)

    if not os.path.isfile(file_path):
        return {"error": "Thumbnail not found"}

    return FileResponse(file_path, media_type="image/jpeg")
