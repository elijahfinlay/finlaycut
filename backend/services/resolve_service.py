"""
Central service for all DaVinci Resolve interactions.
Maintains persistent connection and provides high-level operations.
"""

import asyncio
import time
from typing import Optional
from utils.resolve_connection import get_resolve, get_project_info


class ResolveService:
    def __init__(self):
        self._resolve = None
        self._last_project_info: dict | None = None
        self._connected = False
        self._last_connect_attempt = 0
        self._reconnect_interval = 2.0  # seconds

    @property
    def connected(self) -> bool:
        return self._connected and self._resolve is not None

    @property
    def resolve(self):
        return self._resolve

    def try_connect(self) -> bool:
        """Attempt to connect to DaVinci Resolve."""
        try:
            self._resolve = get_resolve()
            if self._resolve is not None:
                self._connected = True
                self._last_project_info = get_project_info(self._resolve)
                print("[ResolveService] Connected to DaVinci Resolve")
                return True
            else:
                self._connected = False
                return False
        except Exception as e:
            print(f"[ResolveService] Connection failed: {e}")
            self._connected = False
            self._resolve = None
            return False

    def get_status(self) -> dict:
        """Get current connection status and project info."""
        if not self.connected:
            return {
                "connected": False,
                "project": None,
                "timeline": None
            }

        try:
            info = get_project_info(self._resolve)
            if info is None:
                self._connected = False
                return {"connected": False, "project": None, "timeline": None}

            self._last_project_info = info
            return {
                "connected": True,
                "project": {
                    "name": info["project_name"],
                    "timeline_count": info["timeline_count"]
                },
                "timeline": info["current_timeline"]
            }
        except Exception as e:
            print(f"[ResolveService] Error getting status: {e}")
            self._connected = False
            return {"connected": False, "project": None, "timeline": None}

    async def poll_loop(self):
        """Background loop that checks Resolve connection status."""
        while True:
            try:
                if not self.connected:
                    self.try_connect()
                else:
                    # Verify connection is still alive
                    status = self.get_status()
                    if not status["connected"]:
                        print("[ResolveService] Lost connection, will retry...")
                        self.try_connect()

                await asyncio.sleep(self._reconnect_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[ResolveService] Poll error: {e}")
                await asyncio.sleep(self._reconnect_interval)

    def get_project_manager(self):
        """Get the project manager object."""
        if not self.connected:
            return None
        try:
            return self._resolve.GetProjectManager()
        except Exception:
            self._connected = False
            return None

    def get_current_project(self):
        """Get the current project object."""
        pm = self.get_project_manager()
        if pm is None:
            return None
        try:
            return pm.GetCurrentProject()
        except Exception:
            return None

    def get_current_timeline(self):
        """Get the current timeline object."""
        project = self.get_current_project()
        if project is None:
            return None
        try:
            return project.GetCurrentTimeline()
        except Exception:
            return None

    def get_media_pool(self):
        """Get the media pool object."""
        project = self.get_current_project()
        if project is None:
            return None
        try:
            return project.GetMediaPool()
        except Exception:
            return None

    def duplicate_timeline(self, name: str | None = None):
        """
        Duplicate the current timeline as a backup.
        Returns the backup timeline, then switches back to the original.
        """
        project = self.get_current_project()
        timeline = self.get_current_timeline()
        if project is None or timeline is None:
            return None

        backup_name = name or f"{timeline.GetName()}_backup_{int(time.time())}"
        try:
            backup = timeline.DuplicateTimeline(backup_name)
            # Switch back to the original timeline
            project.SetCurrentTimeline(timeline)
            return backup
        except Exception as e:
            print(f"[ResolveService] Failed to duplicate timeline: {e}")
            return None

    def get_timeline_clips(self, track_type: str = "video", track_index: int = 1) -> list:
        """Get all clips on a specific track."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return []
        try:
            items = timeline.GetItemListInTrack(track_type, track_index)
            return items if items else []
        except Exception:
            return []

    def get_clip_file_path(self, timeline_item) -> str | None:
        """Get the source file path for a timeline item."""
        try:
            mpi = timeline_item.GetMediaPoolItem()
            if mpi is None:
                return None
            props = mpi.GetClipProperty()
            return props.get("File Path", None)
        except Exception:
            return None

    def get_timeline_info(self) -> dict | None:
        """Get detailed info about the current timeline."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return None

        try:
            return {
                "name": timeline.GetName(),
                "start_frame": timeline.GetStartFrame(),
                "end_frame": timeline.GetEndFrame(),
                "duration_frames": timeline.GetEndFrame() - timeline.GetStartFrame(),
                "framerate": str(timeline.GetSetting("timelineFrameRate")),
                "resolution_width": timeline.GetSetting("timelineResolutionWidth"),
                "resolution_height": timeline.GetSetting("timelineResolutionHeight"),
                "video_tracks": timeline.GetTrackCount("video"),
                "audio_tracks": timeline.GetTrackCount("audio"),
                "subtitle_tracks": timeline.GetTrackCount("subtitle"),
            }
        except Exception as e:
            print(f"[ResolveService] Error getting timeline info: {e}")
            return None

    def add_marker(self, frame_id: int, color: str, name: str, note: str = "", duration: int = 1) -> bool:
        """Add a marker to the current timeline."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return False
        try:
            return timeline.AddMarker(frame_id, color, name, note, duration)
        except Exception:
            return False

    def get_all_timelines(self) -> list[dict]:
        """Get info about all timelines in the project."""
        project = self.get_current_project()
        if project is None:
            return []

        timelines = []
        try:
            count = project.GetTimelineCount()
            for i in range(1, count + 1):
                tl = project.GetTimelineByIndex(i)
                if tl:
                    timelines.append({
                        "index": i,
                        "name": tl.GetName(),
                    })
        except Exception:
            pass
        return timelines


# Singleton instance
resolve_service = ResolveService()
