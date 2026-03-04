"""Resolve connection status and project info endpoints."""

import subprocess
import logging
import time
from fastapi import APIRouter
from pydantic import BaseModel
from services.resolve_service import resolve_service
from utils.operation_queue import get_current_operation, force_reset_operation

logger = logging.getLogger("finlaycut.resolve_status")

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


@router.get("/timeline-signature")
async def get_timeline_signature():
    """Get a deterministic signature for the current timeline contents."""
    info = resolve_service.get_timeline_signature()
    if info is None:
        return {"error": "No timeline available", "success": False}
    return {"success": True, **info}


@router.get("/timelines")
async def get_all_timelines():
    """Get list of all timelines in the current project."""
    timelines = resolve_service.get_all_timelines()
    return {"timelines": timelines}


@router.get("/playhead")
async def get_playhead():
    """Get the current Resolve playhead position in seconds."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve", "seconds": None}
    seconds = resolve_service.get_playhead_seconds()
    return {"seconds": seconds}


@router.get("/in-out")
async def get_in_out_range():
    """Get the current timeline in/out mark range in seconds."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve", "range": None}
    result = resolve_service.get_in_out_range()
    if result is None:
        return {"range": None}
    return {"range": result}


class InOutRequest(BaseModel):
    in_seconds: float
    out_seconds: float


@router.post("/in-out")
async def set_in_out_range(request: InOutRequest):
    """Set timeline in/out marks from seconds."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    success = resolve_service.set_in_out_range(request.in_seconds, request.out_seconds)
    return {"success": success}


@router.delete("/in-out")
async def clear_in_out_range():
    """Clear timeline in/out marks."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    success = resolve_service.clear_in_out_range()
    return {"success": success}


class PlayheadRequest(BaseModel):
    seconds: float


@router.post("/playhead")
async def set_playhead(request: PlayheadRequest):
    """Move the Resolve playhead to a specific time in seconds."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    success = resolve_service.set_playhead(request.seconds)
    return {"success": success}


class PlayheadFrameRequest(BaseModel):
    frame: int


@router.post("/playhead-frame")
async def set_playhead_frame(request: PlayheadFrameRequest):
    """Move the Resolve playhead to an absolute timeline frame."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}
    success = resolve_service.set_playhead_frame(request.frame)
    return {"success": success}


@router.get("/operation")
async def get_operation_status():
    """Get the currently running heavy operation, if any."""
    op = get_current_operation()
    return {"operation": op}


@router.post("/operation/cancel")
async def cancel_operation():
    """Force-cancel a stuck heavy operation and release the lock."""
    result = force_reset_operation()
    return result


@router.get("/transport-state")
async def get_transport_state():
    """Get Resolve's play/pause state via the global key-event monitor.

    Resolve's GetCurrentTimecode() is completely stale during playback on
    macOS, so we can't detect play/pause via timecode polling.  Instead, a
    background Quartz event tap watches for spacebar presses while Resolve is
    frontmost and toggles an internal flag.
    """
    from services import transport_monitor

    is_playing = transport_monitor.get_is_playing()
    seconds = resolve_service.get_playhead_seconds() if resolve_service.connected else None
    return {"is_playing": is_playing, "seconds": seconds}


class TransportRequest(BaseModel):
    action: str  # "play" or "stop"


_last_transport_time = 0.0


@router.post("/transport")
async def transport_control(request: TransportRequest):
    """Control Resolve playback via AppleScript (spacebar toggle)."""
    global _last_transport_time

    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    # Cooldown: ignore rapid-fire toggles (prevents double-press issue)
    now = time.time()
    if now - _last_transport_time < 0.5:
        return {"success": True, "skipped": True}
    _last_transport_time = now

    try:
        # Send spacebar to Resolve without stealing focus from FinlayCut.
        # Using "tell process" instead of "activate" avoids the focus-switch
        # race condition that caused the double-press bug.
        script = 'tell application "System Events" to tell process "Resolve" to key code 49'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)

        # Sync the transport monitor state (since we sent the key, not the user)
        from services import transport_monitor
        new_state = transport_monitor.toggle_playing()

        return {"success": True, "is_playing": new_state}
    except Exception as e:
        logger.warning("Transport control failed: %s", e)
        return {"error": str(e)}
