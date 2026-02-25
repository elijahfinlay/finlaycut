"""Resolve connection status and project info endpoints."""

from fastapi import APIRouter
from services.resolve_service import resolve_service

router = APIRouter()


@router.get("/status")
async def get_resolve_status():
    """Get current DaVinci Resolve connection status."""
    return resolve_service.get_status()


@router.post("/reconnect")
async def reconnect_resolve():
    """Force a reconnection attempt to DaVinci Resolve."""
    success = resolve_service.try_connect()
    return {
        "success": success,
        "status": resolve_service.get_status()
    }


@router.get("/timeline")
async def get_timeline_info():
    """Get detailed info about the current timeline."""
    info = resolve_service.get_timeline_info()
    if info is None:
        return {"error": "No timeline available", "timeline": None}
    return {"timeline": info}


@router.get("/timelines")
async def get_all_timelines():
    """Get list of all timelines in the current project."""
    timelines = resolve_service.get_all_timelines()
    return {"timelines": timelines}
