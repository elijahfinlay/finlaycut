"""Visual clip search routes."""

import logging
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from api.routes.captions import apply_srt_to_timeline
from api.websocket import manager
from services.clip_search_service import clip_search_service
from services.resolve_service import resolve_service
from utils.operation_queue import heavy_operation

logger = logging.getLogger("finlaycut.clip_search_route")

router = APIRouter()


class ClipSearchAnalyzeRequest(BaseModel):
    track_index: int = Field(default=1, ge=1, le=12)


class ClipSearchQueryRequest(BaseModel):
    query: str = ""
    track_index: int = Field(default=1, ge=1, le=12)
    limit: int = Field(default=50, ge=1, le=250)


class ClipSearchOverlayRequest(BaseModel):
    track_index: int = Field(default=1, ge=1, le=12)


class ClipSearchClearRequest(BaseModel):
    track_index: int = Field(default=1, ge=1, le=12)


@router.get("/status")
async def get_clip_search_status(track_index: int = Query(default=1, ge=1, le=12)):
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    try:
        return {"success": True, **clip_search_service.get_status(track_index=track_index)}
    except Exception as exc:
        logger.warning("Clip search status lookup failed: %s", exc)
        return {"error": str(exc)}


@router.post("/analyze")
@heavy_operation("Clip Search Analysis", timeout_seconds=3600)
async def analyze_clips(request: ClipSearchAnalyzeRequest):
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    try:
        await manager.send_progress("clip_search", 0.02, "Preparing clip analysis...")

        async def progress_fn(progress: float, message: str):
            await manager.send_progress("clip_search", max(0.02, min(progress, 0.99)), message)

        payload = await clip_search_service.analyze_current_timeline(
            track_index=request.track_index,
            progress_cb=progress_fn,
        )
        await manager.send_progress("clip_search", 1.0, "Clip analysis complete")
        await manager.send_complete("clip_search", payload)
        return payload
    except Exception as exc:
        await manager.send_error("clip_search", str(exc))
        return {"error": str(exc)}


@router.post("/query")
async def query_clips(request: ClipSearchQueryRequest):
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    try:
        return {
            "success": True,
            **clip_search_service.query_current_scope(
                request.query,
                track_index=request.track_index,
                limit=request.limit,
            ),
        }
    except Exception as exc:
        logger.warning("Clip search query failed: %s", exc)
        return {"error": str(exc)}


@router.post("/overlay")
async def create_clip_search_overlay(request: ClipSearchOverlayRequest):
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    try:
        srt_content, entry_count = clip_search_service.build_overlay_srt(track_index=request.track_index)
        return await apply_srt_to_timeline(
            operation_name="clip_search_overlay",
            srt_content=srt_content,
            entry_count=entry_count,
            undo_label="Clip Search Overlay",
            undo_description=f"Applied {entry_count} clip search overlays",
            srt_filename="clip_search_overlay.srt",
            ensure_new_track=True,
        )
    except Exception as exc:
        await manager.send_error("clip_search_overlay", str(exc))
        return {"error": str(exc)}


@router.post("/clear")
async def clear_clip_search_scope(request: ClipSearchClearRequest):
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    try:
        payload = clip_search_service.clear_current_scope(track_index=request.track_index)
        return {"success": True, **payload}
    except Exception as exc:
        logger.warning("Clip search clear failed: %s", exc)
        return {"error": str(exc)}
