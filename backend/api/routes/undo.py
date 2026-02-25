"""Undo/history management endpoints."""

import time
import json
from pathlib import Path
from fastapi import APIRouter
from services.resolve_service import resolve_service
from config import settings

router = APIRouter()

# In-memory history (persisted to disk)
HISTORY_FILE = settings.app_data_dir / "undo_history.json"


def _load_history() -> list[dict]:
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    return []


def _save_history(history: list[dict]):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


@router.get("/history")
async def get_undo_history():
    """Get the undo/backup history."""
    history = _load_history()
    return {"history": history}


@router.post("/record")
async def record_operation(operation: str, description: str = ""):
    """Record an operation in the undo history."""
    timeline = resolve_service.get_current_timeline()
    if timeline is None:
        return {"error": "No active timeline"}

    entry = {
        "id": f"undo_{int(time.time())}",
        "operation": operation,
        "timestamp": time.time(),
        "timeline_name": timeline.GetName(),
        "description": description
    }

    history = _load_history()
    history.insert(0, entry)

    # Keep last 50 entries
    history = history[:50]
    _save_history(history)

    return {"success": True, "entry": entry}


@router.post("/rollback/{entry_id}")
async def rollback(entry_id: str):
    """
    Rollback to a backup timeline.
    Switches the current timeline to the backup associated with the entry.
    """
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    history = _load_history()
    entry = next((e for e in history if e["id"] == entry_id), None)
    if entry is None:
        return {"error": f"History entry {entry_id} not found"}

    # Find the backup timeline by name pattern
    project = resolve_service.get_current_project()
    if project is None:
        return {"error": "No project open"}

    backup_name = entry.get("backup_timeline_name")
    if backup_name:
        # Search for the backup timeline
        count = project.GetTimelineCount()
        for i in range(1, count + 1):
            tl = project.GetTimelineByIndex(i)
            if tl and tl.GetName() == backup_name:
                project.SetCurrentTimeline(tl)
                return {
                    "success": True,
                    "message": f"Rolled back to: {backup_name}"
                }

    return {"error": "Backup timeline not found. It may have been deleted."}


@router.delete("/history")
async def clear_history():
    """Clear the undo history."""
    _save_history([])
    return {"success": True, "message": "History cleared"}
