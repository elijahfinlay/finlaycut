"""Scene detection endpoints."""

import logging
from fastapi import APIRouter, Query
from services.resolve_service import resolve_service
from services.scene_detection_service import scene_detection_service
from utils.operation_queue import heavy_operation
from api.websocket import manager

logger = logging.getLogger("finlaycut.scenes")
router = APIRouter()


@router.post("/detect")
@heavy_operation("Scene Detection")
async def detect_scenes(
    threshold: float = Query(default=27.0, ge=5.0, le=100.0),
    use_ffmpeg: bool = Query(default=False),
):
    """
    Detect scene changes in the current timeline video.
    Uses scenedetect library by default, with ffmpeg fallback.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        return {"error": "No video clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        if use_ffmpeg:
            await manager.send_progress("scenes", 0.1, "Detecting scenes with ffmpeg...")
            # ffmpeg threshold is 0-1 scale, convert from 0-100
            ffmpeg_threshold = min(max(threshold / 100.0, 0.05), 0.95)
            scenes = scene_detection_service.detect_scenes_ffmpeg(
                file_path, ffmpeg_threshold
            )
            await manager.send_progress("scenes", 1.0, f"Found {len(scenes)} scenes")
        else:
            async def progress_fn(pct, msg):
                await manager.send_progress("scenes", pct, msg)

            scenes = await scene_detection_service.detect_scenes_with_progress(
                file_path,
                threshold=threshold,
                progress_fn=progress_fn,
            )

        return {
            "scenes": scenes,
            "count": len(scenes),
            "method": "ffmpeg" if use_ffmpeg else "scenedetect",
        }
    except ImportError:
        # scenedetect not installed, try ffmpeg fallback
        logger.warning("scenedetect not installed, falling back to ffmpeg")
        try:
            await manager.send_progress("scenes", 0.1, "Detecting scenes with ffmpeg (fallback)...")
            ffmpeg_threshold = min(max(threshold / 100.0, 0.05), 0.95)
            scenes = scene_detection_service.detect_scenes_ffmpeg(
                file_path, ffmpeg_threshold
            )
            await manager.send_progress("scenes", 1.0, f"Found {len(scenes)} scenes")
            return {
                "scenes": scenes,
                "count": len(scenes),
                "method": "ffmpeg_fallback",
            }
        except Exception as e:
            return {"error": f"Scene detection failed: {e}. Install scenedetect: pip install scenedetect[opencv]"}
    except Exception as e:
        await manager.send_error("scenes", str(e))
        return {"error": str(e)}
