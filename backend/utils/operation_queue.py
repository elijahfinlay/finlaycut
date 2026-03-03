"""
Semaphore-based operation queue to prevent concurrent heavy operations.
Whisper transcription, silence analysis, and similar CPU-intensive tasks
should not run simultaneously.
"""

import asyncio
import logging
import time
from functools import wraps
from fastapi import HTTPException

logger = logging.getLogger("finlaycut.operation_queue")

_heavy_op_semaphore = asyncio.Semaphore(1)
_current_operation: str | None = None
_operation_started_at: float | None = None

# Auto-release stuck operations after this many seconds
_OPERATION_TIMEOUT_SECONDS = 600  # 10 minutes


def _is_operation_stale() -> bool:
    """Check if the current operation has exceeded the timeout."""
    if _operation_started_at is None:
        return False
    return (time.time() - _operation_started_at) > _OPERATION_TIMEOUT_SECONDS


def force_reset_operation() -> dict:
    """Force-clear a stuck operation lock. Returns status info."""
    global _current_operation, _operation_started_at, _heavy_op_semaphore

    prev = _current_operation
    was_locked = _heavy_op_semaphore.locked()

    if was_locked:
        # Replace the semaphore to force-release the lock
        _heavy_op_semaphore = asyncio.Semaphore(1)
        _current_operation = None
        _operation_started_at = None
        logger.warning("Force-reset stuck operation: %s", prev)
        return {"reset": True, "previous_operation": prev}

    _current_operation = None
    _operation_started_at = None
    return {"reset": False, "message": "No operation was running"}


def heavy_operation(operation_name: str):
    """Decorator for route handlers that perform heavy operations.
    Returns HTTP 409 if another heavy operation is already running.
    Auto-resets if the current operation has exceeded the timeout."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            global _current_operation, _operation_started_at, _heavy_op_semaphore

            # Auto-reset stale operations
            if _heavy_op_semaphore.locked() and _is_operation_stale():
                stale_op = _current_operation
                logger.warning(
                    "Auto-resetting stale operation '%s' (started %.0fs ago)",
                    stale_op, time.time() - (_operation_started_at or 0),
                )
                _heavy_op_semaphore = asyncio.Semaphore(1)
                _current_operation = None
                _operation_started_at = None

            if _heavy_op_semaphore.locked():
                raise HTTPException(
                    status_code=409,
                    detail=f"Another operation is already running: {_current_operation or 'unknown'}. Please wait for it to finish."
                )
            async with _heavy_op_semaphore:
                _current_operation = operation_name
                _operation_started_at = time.time()
                try:
                    return await func(*args, **kwargs)
                finally:
                    _current_operation = None
                    _operation_started_at = None
        return wrapper
    return decorator


def get_current_operation() -> dict | None:
    """Get info about the currently running heavy operation, if any."""
    if not _heavy_op_semaphore.locked():
        return None
    elapsed = time.time() - _operation_started_at if _operation_started_at else 0
    return {
        "name": _current_operation,
        "elapsed_seconds": round(elapsed, 1),
        "stale": _is_operation_stale(),
    }
