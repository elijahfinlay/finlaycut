"""Timeline export endpoints: EDL, FCPXML."""

import logging
import os
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from services.resolve_service import resolve_service
from services.export_service import export_service
from api.websocket import manager

logger = logging.getLogger("finlaycut.export_route")
router = APIRouter()


class ExportRequest(BaseModel):
    format: str = "edl"  # "edl" or "fcpxml"


@router.post("/generate")
async def generate_export(request: ExportRequest):
    """Export the current timeline as EDL or FCPXML."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    try:
        await manager.send_progress("export", 0.1, "Reading timeline...")

        timeline_name = timeline.GetName() or "Timeline"
        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0
        tl_start = int(timeline.GetStartFrame())
        tl_end = int(timeline.GetEndFrame())
        duration = (tl_end - tl_start) / fps

        # Gather all video clips from track 1
        video_clips = resolve_service.get_timeline_clips("video", 1)
        if not video_clips:
            return {"error": "No video clips found in timeline"}

        await manager.send_progress("export", 0.3, "Processing clips...")

        clip_data = []
        for i, clip in enumerate(video_clips):
            name = clip.GetName() if hasattr(clip, "GetName") else f"Clip_{i+1}"
            clip_start = int(clip.GetStart())
            clip_end = int(clip.GetEnd())

            rec_start_s = (clip_start - tl_start) / fps
            rec_end_s = (clip_end - tl_start) / fps

            # Try to get source in/out
            src_start_s = 0.0
            src_end_s = rec_end_s - rec_start_s
            try:
                source_range = resolve_service.get_timeline_item_source_range(clip)
                if source_range:
                    src_start_s = source_range["source_start_frame"] / fps
                    src_end_s = source_range["source_end_frame"] / fps
            except Exception:
                pass

            clip_data.append({
                "name": name,
                "record_start": rec_start_s,
                "record_end": rec_end_s,
                "source_start": src_start_s,
                "source_end": src_end_s,
                "track_type": "V",
            })

        await manager.send_progress("export", 0.6, f"Generating {request.format.upper()}...")

        if request.format == "fcpxml":
            output_path = export_service.generate_fcpxml(
                clip_data, timeline_name, fps, duration
            )
        else:
            output_path = export_service.generate_edl(
                clip_data, timeline_name, fps
            )

        await manager.send_progress("export", 1.0, "Export complete")

        filename = os.path.basename(output_path)
        return {
            "path": output_path,
            "filename": filename,
            "format": request.format,
            "clip_count": len(clip_data),
            "timeline_name": timeline_name,
        }
    except Exception as e:
        await manager.send_error("export", str(e))
        return {"error": str(e)}


@router.get("/download/{filename}")
async def download_export(filename: str):
    """Download a generated export file."""
    safe_name = os.path.basename(filename)
    file_path = os.path.join(str(export_service.export_dir), safe_name)

    if not os.path.isfile(file_path):
        return {"error": "Export file not found"}

    media_type = "text/plain"
    if safe_name.endswith(".fcpxml"):
        media_type = "application/xml"

    return FileResponse(file_path, media_type=media_type, filename=safe_name)
