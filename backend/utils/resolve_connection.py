"""
Robust connection logic for DaVinci Resolve scripting API.
Works with both Free and Studio versions via external scripting (Network mode).
"""

import sys
import os
import importlib.util
import importlib.machinery


def _load_dynamic(module_name: str, file_path: str):
    """Load a native extension module dynamically."""
    loader = importlib.machinery.ExtensionFileLoader(module_name, file_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        return None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _load_source(module_name: str, file_path: str):
    """Load a Python source module dynamically."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def get_resolve():
    """
    Connect to DaVinci Resolve and return the Resolve object.
    Returns None if Resolve is not running or not reachable.
    """
    try:
        import DaVinciResolveScript as bmd
    except ImportError:
        # Try loading from the standard macOS path
        script_api = os.getenv(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
        )
        modules_path = os.path.join(script_api, "Modules", "DaVinciResolveScript.py")

        try:
            _load_source("DaVinciResolveScript", modules_path)
            import DaVinciResolveScript as bmd
        except Exception as e:
            print(f"[ResolveConnection] Could not load DaVinciResolveScript: {e}")
            return None

    try:
        resolve = bmd.scriptapp("Resolve")
        return resolve
    except Exception as e:
        print(f"[ResolveConnection] Could not connect to Resolve: {e}")
        return None


def get_project_info(resolve) -> dict | None:
    """Get current project information from Resolve."""
    if resolve is None:
        return None

    try:
        pm = resolve.GetProjectManager()
        if pm is None:
            return None

        project = pm.GetCurrentProject()
        if project is None:
            return None

        timeline = project.GetCurrentTimeline()
        timeline_info = None
        if timeline:
            timeline_info = {
                "name": timeline.GetName(),
                "duration": timeline.GetEndFrame() - timeline.GetStartFrame(),
                "start_frame": timeline.GetStartFrame(),
                "end_frame": timeline.GetEndFrame(),
                "video_tracks": timeline.GetTrackCount("video"),
                "audio_tracks": timeline.GetTrackCount("audio"),
                "subtitle_tracks": timeline.GetTrackCount("subtitle"),
                "framerate": str(timeline.GetSetting("timelineFrameRate")),
                "resolution": f"{timeline.GetSetting('timelineResolutionWidth')}x{timeline.GetSetting('timelineResolutionHeight')}"
            }

        return {
            "project_name": project.GetName(),
            "timeline_count": project.GetTimelineCount(),
            "current_timeline": timeline_info
        }
    except Exception as e:
        print(f"[ResolveConnection] Error getting project info: {e}")
        return None
