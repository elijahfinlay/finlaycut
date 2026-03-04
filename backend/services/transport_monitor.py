"""Monitor Resolve's transport state via macOS global key events.

DaVinci Resolve's scripting API (GetCurrentTimecode) returns stale values
during active playback on macOS, making it impossible to detect play/pause
via timecode polling.  This module uses a Quartz event tap to listen for
spacebar presses while Resolve is the frontmost app, and pushes state
changes over WebSocket to the frontend instantly.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger("finlaycut.transport_monitor")

# --- State ---
_is_playing = False
_lock = threading.Lock()
_monitor_thread: Optional[threading.Thread] = None
_running = False
# Timestamp of the last API-initiated toggle (to suppress the event tap echo)
_last_api_toggle: float = 0.0
# asyncio event loop from the main thread (for broadcasting from the tap thread)
_loop: Optional[asyncio.AbstractEventLoop] = None

SPACEBAR_KEYCODE = 49
# Ignore tap events within this window after an API toggle (prevents double-toggle)
API_SUPPRESS_WINDOW = 0.8  # seconds


def get_is_playing() -> bool:
    with _lock:
        return _is_playing


def set_is_playing(value: bool) -> None:
    global _is_playing
    with _lock:
        _is_playing = value


def toggle_playing() -> bool:
    """Toggle play state and return the new value.  Called by the /transport
    endpoint when FinlayCut sends spacebar to Resolve via AppleScript."""
    global _is_playing, _last_api_toggle
    with _lock:
        _is_playing = not _is_playing
        _last_api_toggle = time.monotonic()
        new_state = _is_playing
    _broadcast_state(new_state)
    return new_state


def _broadcast_state(is_playing: bool) -> None:
    """Push transport state to all connected WebSocket clients."""
    if _loop is None:
        return
    try:
        from api.websocket import manager

        async def _send():
            try:
                seconds = None
                try:
                    from services.resolve_service import resolve_service
                    if resolve_service.connected:
                        seconds = resolve_service.get_playhead_seconds()
                except Exception:
                    pass
                await manager.broadcast({
                    "type": "transport_state",
                    "is_playing": is_playing,
                    "seconds": seconds,
                })
            except Exception as e:
                logger.debug("Broadcast transport state failed: %s", e)

        _loop.call_soon_threadsafe(asyncio.ensure_future, _send())
    except Exception as e:
        logger.debug("Failed to schedule broadcast: %s", e)


def _resolve_is_frontmost() -> bool:
    """Check if DaVinci Resolve is the frontmost application."""
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return False
        name = app.localizedName() or ""
        return "resolve" in name.lower()
    except Exception:
        return False


def _event_tap_callback(proxy, event_type, event, refcon):
    """Called for every key-down event system-wide (listen-only tap)."""
    global _is_playing
    try:
        from Quartz import CGEventGetIntegerValueField, kCGKeyboardEventKeycode

        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        if keycode == SPACEBAR_KEYCODE and _resolve_is_frontmost():
            # Skip if this is the echo of an API-initiated toggle
            with _lock:
                if time.monotonic() - _last_api_toggle < API_SUPPRESS_WINDOW:
                    return event

                _is_playing = not _is_playing
                new_state = _is_playing

            logger.info("Spacebar in Resolve detected — is_playing=%s", new_state)
            _broadcast_state(new_state)
    except Exception as e:
        logger.debug("Event tap callback error: %s", e)

    return event


def _run_event_tap():
    """Run the Quartz event tap on a dedicated thread with its own run loop."""
    global _running
    try:
        from Quartz import (
            CGEventTapCreate,
            CGEventTapEnable,
            kCGEventKeyDown,
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            CFMachPortCreateRunLoopSource,
            CFRunLoopGetCurrent,
            CFRunLoopAddSource,
            CFRunLoopRun,
            kCFRunLoopCommonModes,
        )

        # Create a passive (listen-only) event tap for key-down events
        tap = CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            1 << kCGEventKeyDown,
            _event_tap_callback,
            None,
        )

        if tap is None:
            logger.error(
                "Could not create event tap — grant Accessibility permission to "
                "Terminal / Python in System Settings > Privacy & Security > Accessibility"
            )
            _running = False
            return

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(loop, source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)

        logger.info("Transport monitor started (global key event tap active)")
        _running = True
        CFRunLoopRun()  # Blocks until stopped

    except Exception as e:
        logger.error("Transport monitor failed: %s", e)
    finally:
        _running = False
        logger.info("Transport monitor stopped")


def start(loop: Optional[asyncio.AbstractEventLoop] = None):
    """Start the background transport monitor thread."""
    global _monitor_thread, _loop
    _loop = loop
    if _monitor_thread is not None and _monitor_thread.is_alive():
        return

    _monitor_thread = threading.Thread(target=_run_event_tap, daemon=True, name="transport-monitor")
    _monitor_thread.start()


def stop():
    """Stop the transport monitor (best-effort)."""
    global _running
    _running = False
