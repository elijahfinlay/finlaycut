"""
Central service for all DaVinci Resolve interactions.
Maintains persistent connection and provides high-level operations.
"""

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Optional
from config import settings
from utils.resolve_connection import get_resolve, get_project_info

logger = logging.getLogger("finlaycut.resolve_service")


class ResolveService:
    def __init__(self):
        self._resolve = None
        self._last_project_info: dict | None = None
        self._connected = False
        self._last_connect_attempt = 0
        self._reconnect_interval = 2.0  # seconds
        self._signature_poll_counter: int = 0

    def _debug_enabled(self) -> bool:
        return bool(getattr(settings, "debug_cut_planning", False))

    def _debug(self, message: str):
        if self._debug_enabled():
            logger.debug(message)

    @property
    def connected(self) -> bool:
        if self._connected and self._resolve is not None:
            return True

        # Resolve occasionally drops a single scripting call even while the app
        # is still open. If we still have a Resolve handle, retry immediately.
        if self._resolve is not None:
            return self.try_connect()

        return False

    @property
    def resolve(self):
        return self._resolve

    def try_connect(self) -> bool:
        """Attempt to connect to DaVinci Resolve."""
        self._last_connect_attempt = time.monotonic()
        try:
            self._resolve = get_resolve()
            if self._resolve is not None:
                self._connected = True
                self._last_project_info = get_project_info(self._resolve)
                logger.info("Connected to DaVinci Resolve")
                return True
            else:
                self._connected = False
                return False
        except Exception as e:
            logger.error("Connection failed: %s", e)
            self._connected = False
            self._resolve = None
            return False

    def _serialize_timeline_info(self, timeline) -> dict | None:
        if timeline is None:
            return None

        try:
            return {
                "name": timeline.GetName(),
                "duration": timeline.GetEndFrame() - timeline.GetStartFrame(),
                "start_frame": timeline.GetStartFrame(),
                "end_frame": timeline.GetEndFrame(),
                "video_tracks": timeline.GetTrackCount("video"),
                "audio_tracks": timeline.GetTrackCount("audio"),
                "subtitle_tracks": timeline.GetTrackCount("subtitle"),
                "framerate": str(timeline.GetSetting("timelineFrameRate")),
                "resolution": (
                    f"{timeline.GetSetting('timelineResolutionWidth')}"
                    f"x{timeline.GetSetting('timelineResolutionHeight')}"
                ),
            }
        except Exception as exc:
            logger.warning("Failed to serialize timeline info: %s", exc)
            return None

    def _get_cached_timeline_name(self, project_name: str | None = None) -> str | None:
        info = self._last_project_info or {}
        cached_project_name = info.get("project_name")
        if project_name and cached_project_name and cached_project_name != project_name:
            return None

        timeline = info.get("current_timeline") or {}
        name = timeline.get("name")
        if isinstance(name, str) and name.strip():
            return name
        return None

    def _find_timeline_by_name(self, project, timeline_name: str | None):
        if project is None or not timeline_name:
            return None

        try:
            timeline_count = int(project.GetTimelineCount() or 0)
        except Exception as exc:
            logger.warning("Failed to enumerate Resolve timelines: %s", exc)
            return None

        for index in range(1, timeline_count + 1):
            try:
                timeline = project.GetTimelineByIndex(index)
            except Exception:
                continue
            if timeline is None:
                continue
            try:
                if timeline.GetName() == timeline_name:
                    return timeline
            except Exception:
                continue

        return None

    def get_status(self) -> dict:
        """Get current connection status and project info."""
        if not self.connected:
            return {
                "connected": False,
                "project": None,
                "timeline": None,
                "using_cached": False,
            }

        try:
            using_cached = False
            info = get_project_info(self._resolve)
            if info is None:
                info = self._last_project_info
                if info is None:
                    self._connected = False
                    return {"connected": False, "project": None, "timeline": None, "using_cached": False}
                logger.warning("Resolve status lookup returned no project info; using cached status")
                using_cached = True

            if info.get("current_timeline") is None:
                cached_timeline = self._last_project_info.get("current_timeline") if self._last_project_info else None
                recovered_timeline = self.get_current_timeline()
                recovered_info = self._serialize_timeline_info(recovered_timeline)
                if recovered_info is not None:
                    info = {
                        **info,
                        "current_timeline": recovered_info,
                    }
                    using_cached = True
                elif cached_timeline and cached_timeline.get("name"):
                    logger.warning("Resolve returned no current timeline; using cached timeline info")
                    info = {
                        **info,
                        "current_timeline": cached_timeline,
                    }
                    using_cached = True

            self._last_project_info = info
            return {
                "connected": True,
                "project": {
                        "name": info["project_name"],
                        "timeline_count": info["timeline_count"]
                    },
                "timeline": info["current_timeline"],
                "using_cached": using_cached,
            }
        except Exception as e:
            logger.warning("Error getting Resolve status; using cached project info if available: %s", e)
            if self._last_project_info is not None:
                info = self._last_project_info
                return {
                    "connected": True,
                    "project": {
                        "name": info["project_name"],
                        "timeline_count": info["timeline_count"]
                    },
                    "timeline": info["current_timeline"],
                    "using_cached": True,
                }
            self._connected = False
            return {"connected": False, "project": None, "timeline": None, "using_cached": False}

    async def _check_timeline_signature(self, ws_manager):
        """Compare the current timeline signature and notify subscribed clients."""
        if not self.connected or not ws_manager.has_signature_watches():
            return
        try:
            sig_data = self.get_timeline_signature()
            if sig_data is None:
                return
            await ws_manager.notify_timeline_watchers(
                sig_data["timeline_signature"],
                sig_data.get("timeline_name", ""),
            )
        except Exception as e:
            logger.debug("Signature check error: %s", e)

    async def poll_loop(self):
        """Background loop that checks Resolve connection status."""
        from api.websocket import manager as ws_manager

        while True:
            try:
                if not self.connected:
                    await asyncio.to_thread(self.try_connect)
                else:
                    # Verify connection is still alive
                    status = await asyncio.to_thread(self.get_status)
                    if not status["connected"] or status.get("using_cached"):
                        if status.get("using_cached"):
                            logger.warning("Resolve status is stale, refreshing connection...")
                        else:
                            logger.warning("Lost connection, will retry...")
                        await asyncio.to_thread(self.try_connect)

                    # Check timeline signature every 2nd cycle (~4s)
                    if ws_manager.has_signature_watches():
                        self._signature_poll_counter += 1
                        if self._signature_poll_counter >= 2:
                            self._signature_poll_counter = 0
                            await self._check_timeline_signature(ws_manager)
                    else:
                        self._signature_poll_counter = 0

                await asyncio.sleep(self._reconnect_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Poll error: %s", e)
                await asyncio.sleep(self._reconnect_interval)

    def get_project_manager(self):
        """Get the project manager object."""
        if not self.connected:
            return None
        try:
            return self._resolve.GetProjectManager()
        except Exception as exc:
            logger.warning("Resolve project manager lookup failed, retrying connection: %s", exc)
            self._connected = False
            if not self.try_connect() or self._resolve is None:
                return None
            try:
                return self._resolve.GetProjectManager()
            except Exception as retry_exc:
                logger.warning("Resolve project manager retry failed: %s", retry_exc)
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
            timeline = project.GetCurrentTimeline()
            if timeline is not None:
                return timeline
        except Exception as exc:
            logger.warning("Resolve current timeline lookup failed: %s", exc)

        project_name = None
        try:
            project_name = project.GetName()
        except Exception:
            project_name = None

        cached_timeline_name = self._get_cached_timeline_name(project_name)
        if cached_timeline_name:
            fallback = self._find_timeline_by_name(project, cached_timeline_name)
            if fallback is not None:
                logger.warning(
                    "Resolve returned no current timeline; falling back to cached timeline %s",
                    cached_timeline_name,
                )
                return fallback

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

        Per Resolve API docs: DuplicateTimeline(timelineName) is a
        Timeline method that takes an optional string name.
        """
        project = self.get_current_project()
        timeline = self.get_current_timeline()
        if project is None or timeline is None:
            return None

        backup_name = name or f"{timeline.GetName()}_backup_{int(time.time())}"
        backup = None

        try:
            # Resolve API: timeline.DuplicateTimeline(timelineName) -> timeline
            backup = timeline.DuplicateTimeline(backup_name)
        except Exception as e:
            logger.warning("DuplicateTimeline(name) failed: %s", e)

        if backup is None:
            # Try without the name argument (the name is optional)
            try:
                backup = timeline.DuplicateTimeline()
            except Exception as e:
                logger.warning("DuplicateTimeline() failed: %s", e)

        if backup is None:
            logger.warning("DuplicateTimeline failed — could not create backup")
            return None

        # Switch back to the original timeline so subsequent operations
        # work on the original, not the backup
        try:
            project.SetCurrentTimeline(timeline)
        except Exception:
            pass

        return backup

    def _timeline_rate_info(self, timeline) -> dict:
        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0
        nominal_fps = int(round(fps))
        if abs(fps - 29.97) < 0.02:
            nominal_fps = 30
        elif abs(fps - 59.94) < 0.02:
            nominal_fps = 60
        elif abs(fps - 23.976) < 0.02:
            nominal_fps = 24

        drop_setting = str(timeline.GetSetting("timelineDropFrameTimecode") or "").strip().lower()
        start_tc = timeline.GetStartTimecode() or "00:00:00:00"
        is_drop = drop_setting in {"1", "true", "yes", "on"} or ";" in start_tc

        return {
            "fps": fps,
            "nominal_fps": nominal_fps,
            "drop_frame": is_drop,
            "start_tc": start_tc,
            "start_frame": int(timeline.GetStartFrame()),
        }

    def set_playhead(self, seconds: float) -> bool:
        """Move the timeline playhead to a position in seconds from timeline start."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return False
        try:
            info = self._timeline_rate_info(timeline)
            target_timeline_frame = info["start_frame"] + int(round(seconds * info["fps"]))
            return self.set_playhead_frame(target_timeline_frame)
        except Exception as e:
            logger.error("Failed to set playhead: %s", e)
            return False

    def get_playhead_seconds(self) -> float | None:
        """Get the current playhead position in seconds from timeline start."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return None
        try:
            info = self._timeline_rate_info(timeline)
            current_tc = timeline.GetCurrentTimecode()
            if not current_tc:
                return None
            start_tc_frames = self._timecode_to_frames(
                info["start_tc"], info["fps"], drop_frame=info["drop_frame"]
            )
            current_tc_frames = self._timecode_to_frames(
                current_tc, info["fps"], drop_frame=info["drop_frame"]
            )
            current_frame = info["start_frame"] + (current_tc_frames - start_tc_frames)
            seconds = (current_frame - info["start_frame"]) / info["fps"]
            return round(seconds, 4)
        except Exception as e:
            logger.error("Failed to get playhead: %s", e)
            return None

    def set_playhead_frame(self, timeline_frame: int) -> bool:
        """Move the timeline playhead to an absolute timeline frame."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return False

        try:
            info = self._timeline_rate_info(timeline)
            timeline_end = int(timeline.GetEndFrame())
            clamped_frame = max(info["start_frame"], min(int(timeline_frame), timeline_end - 1))
            start_tc_frames = self._timecode_to_frames(
                info["start_tc"], info["fps"], drop_frame=info["drop_frame"]
            )
            frame_delta = clamped_frame - info["start_frame"]
            target_tc_frames = max(0, start_tc_frames + frame_delta)
            target_tc = self._frames_to_timecode(
                target_tc_frames, info["fps"], drop_frame=info["drop_frame"]
            )

            logger.debug("Jump frame %s -> %s", clamped_frame, target_tc)
            return bool(timeline.SetCurrentTimecode(target_tc))
        except Exception as e:
            logger.error("Failed to set playhead frame: %s", e)
            return False

    def _frames_to_timecode(self, total_frames: int, fps: float, drop_frame: bool = False) -> str:
        """Convert frame count to timecode. Supports drop-frame rates."""
        nominal = int(round(fps))
        if abs(fps - 29.97) < 0.02:
            nominal = 30
        elif abs(fps - 59.94) < 0.02:
            nominal = 60

        total_frames = max(0, int(total_frames))

        if not drop_frame:
            ff = total_frames % nominal
            total_seconds = total_frames // nominal
            ss = total_seconds % 60
            total_minutes = total_seconds // 60
            mm = total_minutes % 60
            hh = total_minutes // 60
            return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

        # SMPTE drop-frame (29.97 -> drop 2 frames/min except each 10th;
        # 59.94 -> drop 4 frames/min except each 10th)
        drop_frames = 2 if nominal == 30 else 4 if nominal == 60 else 0
        if drop_frames == 0:
            return self._frames_to_timecode(total_frames, fps, drop_frame=False)

        frames_per_hour = nominal * 3600 - drop_frames * 54
        frames_per_10_minutes = nominal * 600 - drop_frames * 9
        frames_per_minute = nominal * 60 - drop_frames

        fr = total_frames
        hours = fr // frames_per_hour
        fr %= frames_per_hour

        tens_of_minutes = fr // frames_per_10_minutes
        minutes = tens_of_minutes * 10
        fr %= frames_per_10_minutes

        for _ in range(10):
            minute_len = nominal * 60 if minutes % 10 == 0 else frames_per_minute
            if fr >= minute_len:
                fr -= minute_len
                minutes += 1
            else:
                break

        seconds = fr // nominal
        frames = fr % nominal
        return f"{hours:02d}:{minutes:02d}:{seconds:02d};{frames:02d}"

    def _timecode_to_frames(self, tc: str, fps: float, drop_frame: bool = False) -> int:
        """Convert HH:MM:SS:FF / HH:MM:SS;FF timecode to frame count."""
        nominal = int(round(fps))
        if abs(fps - 29.97) < 0.02:
            nominal = 30
        elif abs(fps - 59.94) < 0.02:
            nominal = 60

        tc_str = str(tc or "00:00:00:00").strip()
        tc_drop = ";" in tc_str
        parts = tc_str.replace(";", ":").split(":")
        if len(parts) != 4:
            return 0

        hh, mm, ss, ff = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        total_minutes = hh * 60 + mm
        base_frames = ((hh * 3600 + mm * 60 + ss) * nominal) + ff

        if drop_frame or tc_drop:
            drop_frames = 2 if nominal == 30 else 4 if nominal == 60 else 0
            if drop_frames > 0:
                dropped = drop_frames * (total_minutes - total_minutes // 10)
                base_frames -= dropped

        return max(0, base_frames)

    def get_in_out_range(self) -> dict | None:
        """Get the timeline in/out mark range in seconds.
        Returns {"in_seconds": float, "out_seconds": float, "in_frame": int, "out_frame": int}
        or None if no in/out marks are set.
        """
        timeline = self.get_current_timeline()
        if timeline is None:
            return None
        try:
            marks = timeline.GetMarkInOut()
            if not marks:
                return None

            # GetMarkInOut returns e.g. {'video': {'in': 86400, 'out': 86520}, 'audio': {...}}
            # Frame numbers are absolute (include timeline start offset)
            video_marks = marks.get("video") or marks.get("audio")
            if not video_marks:
                return None

            in_frame = video_marks.get("in")
            out_frame = video_marks.get("out")
            if in_frame is None or out_frame is None:
                return None

            in_frame = int(in_frame)
            out_frame = int(out_frame)

            info = self._timeline_rate_info(timeline)
            start_frame = info["start_frame"]
            fps = info["fps"]

            in_seconds = max(0.0, (in_frame - start_frame) / fps)
            out_seconds = max(0.0, (out_frame - start_frame) / fps)

            if out_seconds <= in_seconds:
                return None

            return {
                "in_seconds": round(in_seconds, 4),
                "out_seconds": round(out_seconds, 4),
                "in_frame": in_frame,
                "out_frame": out_frame,
            }
        except Exception as e:
            logger.error("Failed to get in/out marks: %s", e)
            return None

    def set_in_out_range(self, in_seconds: float, out_seconds: float) -> bool:
        """Set timeline in/out marks by moving playhead and sending I/O keystrokes.

        DaVinci Resolve's scripting API has no SetMarkInOut method, so we:
        1. Save current playhead position
        2. Move to in-point → send 'I' key
        3. Move to out-point → send 'O' key
        4. Restore original playhead position
        """
        import subprocess

        timeline = self.get_current_timeline()
        if timeline is None:
            return False
        try:
            info = self._timeline_rate_info(timeline)
            start_frame = info["start_frame"]
            fps = info["fps"]
            in_frame = int(round(start_frame + in_seconds * fps))
            out_frame = int(round(start_frame + out_seconds * fps))

            # Save current playhead
            saved_tc = timeline.GetCurrentTimecode()

            # Move to in-point and set mark
            self.set_playhead_frame(in_frame)
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" to key code 34'
            ], capture_output=True, timeout=2)

            # Move to out-point and set mark
            self.set_playhead_frame(out_frame)
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" to key code 31'
            ], capture_output=True, timeout=2)

            # Restore playhead
            if saved_tc:
                timeline.SetCurrentTimecode(saved_tc)

            return True
        except Exception as e:
            logger.error("Failed to set in/out marks: %s", e)
            return False

    def clear_in_out_range(self) -> bool:
        """Clear timeline in/out marks via Option+X shortcut."""
        import subprocess

        timeline = self.get_current_timeline()
        if timeline is None:
            return False
        try:
            # Option+X clears in/out marks in Resolve
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" to key code 7 using option down'
            ], capture_output=True, timeout=2)
            return True
        except Exception as e:
            logger.error("Failed to clear in/out marks: %s", e)
            return False

    def select_all_and_close_gaps(self) -> bool:
        """Select all clips on the timeline and close gaps (Cmd+A then Cmd+G).
        Equivalent to Edit > Select All then Timeline > Close Gap."""
        import subprocess
        import time as _time

        timeline = self.get_current_timeline()
        if timeline is None:
            return False
        try:
            # First bring Resolve to focus
            subprocess.run(["osascript", "-e",
                'tell application "DaVinci Resolve" to activate'
            ], capture_output=True, timeout=2)
            _time.sleep(0.2)

            # Cmd+A = Select All
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" '
                'to key code 0 using {command down}'  # key code 0 = A
            ], capture_output=True, timeout=2)
            _time.sleep(0.15)

            # Cmd+G = Close Gap (DaVinci Resolve shortcut: Timeline > Close Gap)
            # Note: This may not work if no gaps exist; that's fine.
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" '
                'to key code 5 using {command down}'  # key code 5 = G
            ], capture_output=True, timeout=2)
            _time.sleep(0.15)

            # Click somewhere neutral to deselect (Cmd+Shift+A = Deselect All)
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" '
                'to key code 0 using {command down, shift down}'  # Cmd+Shift+A
            ], capture_output=True, timeout=2)

            logger.info("Select all + close gaps executed")
            return True
        except Exception as e:
            logger.error("Failed to close gaps: %s", e)
            return False

    def get_media_pool_item_unique_id(self, timeline_item) -> str | None:
        """Return the underlying media pool item's unique ID if available."""
        if timeline_item is None:
            return None
        try:
            media_pool_item = timeline_item.GetMediaPoolItem()
        except Exception:
            return None
        if media_pool_item is None:
            return None

        for method_name in ("GetUniqueId", "GetMediaId", "GetClipProperty"):
            method = getattr(media_pool_item, method_name, None)
            if not callable(method):
                continue
            try:
                value = method() if method_name != "GetClipProperty" else method("MediaId")
            except Exception:
                continue
            if value is None:
                continue
            value_str = str(value).strip()
            if value_str:
                return value_str
        return None

    @staticmethod
    def _timeline_records_overlap_frames(record_a: dict, record_b: dict) -> int:
        try:
            start = max(int(record_a["clip_start_frame"]), int(record_b["clip_start_frame"]))
            end = min(int(record_a["clip_end_frame"]), int(record_b["clip_end_frame"]))
        except Exception:
            return 0
        return max(0, end - start)

    def _link_candidate_score(self, video_record: dict, audio_record: dict) -> tuple | None:
        overlap_frames = self._timeline_records_overlap_frames(video_record, audio_record)
        if overlap_frames <= 0:
            return None

        video_media_id = video_record.get("media_pool_item_unique_id")
        audio_media_id = audio_record.get("media_pool_item_unique_id")
        same_media_pool_item = bool(video_media_id and audio_media_id and video_media_id == audio_media_id)

        video_path = video_record.get("file_path")
        audio_path = audio_record.get("file_path")
        same_file_path = bool(video_path and audio_path and video_path == audio_path)

        if not same_media_pool_item and not same_file_path:
            return None

        video_source_start = video_record.get("source_start_frame")
        video_source_end = video_record.get("source_end_frame")
        audio_source_start = audio_record.get("source_start_frame")
        audio_source_end = audio_record.get("source_end_frame")
        same_source_span = (
            video_source_start is not None
            and video_source_end is not None
            and audio_source_start is not None
            and audio_source_end is not None
            and int(video_source_start) == int(audio_source_start)
            and int(video_source_end) == int(audio_source_end)
        )

        clip_start_delta = abs(int(video_record["clip_start_frame"]) - int(audio_record["clip_start_frame"]))
        clip_end_delta = abs(int(video_record["clip_end_frame"]) - int(audio_record["clip_end_frame"]))

        return (
            1 if same_media_pool_item else 0,
            1 if same_source_span else 0,
            1 if same_file_path else 0,
            overlap_frames,
            -clip_start_delta,
            -clip_end_delta,
            -int(audio_record.get("track_index") or 0),
        )

    def link_all_clips(self) -> dict:
        """
        Link each video clip to the best matching overlapping audio clip on the
        current timeline using Resolve's scripting API.
        """
        timeline = self.get_current_timeline()
        if timeline is None:
            return {"success": False, "error": "No active timeline"}

        set_clips_linked = getattr(timeline, "SetClipsLinked", None)
        if not callable(set_clips_linked):
            return {"success": False, "error": "Resolve does not expose clip-linking through the scripting API"}

        try:
            records = self._get_timeline_item_records()
            enriched_records: list[dict] = []
            for record in records:
                enriched = dict(record)
                enriched["media_pool_item_unique_id"] = self.get_media_pool_item_unique_id(record.get("item"))
                enriched_records.append(enriched)

            video_records = sorted(
                [record for record in enriched_records if record.get("media_type") == "video"],
                key=lambda record: (
                    int(record.get("clip_start_frame") or 0),
                    int(record.get("track_index") or 0),
                ),
            )
            audio_records = [
                record for record in enriched_records
                if record.get("media_type") == "audio"
            ]

            if not video_records or not audio_records:
                return {
                    "success": False,
                    "error": "Need at least one video clip and one audio clip on the current timeline",
                }

            linked_pairs = 0
            used_audio_item_ids: set[int] = set()
            for video_record in video_records:
                best_audio_record = None
                best_score = None
                for audio_record in audio_records:
                    audio_item = audio_record.get("item")
                    if audio_item is None:
                        continue
                    audio_item_id = id(audio_item)
                    if audio_item_id in used_audio_item_ids:
                        continue

                    score = self._link_candidate_score(video_record, audio_record)
                    if score is None:
                        continue
                    if best_score is None or score > best_score:
                        best_score = score
                        best_audio_record = audio_record

                if best_audio_record is None:
                    continue

                video_item = video_record.get("item")
                audio_item = best_audio_record.get("item")
                if video_item is None or audio_item is None:
                    continue

                linked = bool(set_clips_linked([video_item, audio_item], True))
                if not linked:
                    logger.warning(
                        "SetClipsLinked failed for video clip starting at frame %s and audio clip starting at frame %s",
                        video_record.get("clip_start_frame"),
                        best_audio_record.get("clip_start_frame"),
                    )
                    continue

                used_audio_item_ids.add(id(audio_item))
                linked_pairs += 1

            if linked_pairs == 0:
                return {
                    "success": False,
                    "error": "Could not find any matching video/audio clip pairs to link",
                }

            logger.info("Linked %d matching video/audio clip pairs", linked_pairs)
            return {
                "success": True,
                "linked_pairs": linked_pairs,
            }
        except Exception as e:
            logger.error("Failed to link clips pairwise: %s", e)
            return {"success": False, "error": str(e)}

    def apply_ripple_cuts(self, cut_segments: list[dict]) -> dict:
        """Apply ripple cuts using Resolve's blade tool + ripple delete.

        Instead of the fragile delete-all-then-reinsert approach, this:
        1. Blades (splits) clips at each cut boundary using Cmd+B
        2. Finds the resulting clips within each cut range
        3. Deletes them with ripple (closing the gap)

        Cuts are processed in reverse chronological order so that earlier
        positions aren't shifted by later deletions.
        """
        import subprocess
        import time as _time

        timeline = self.get_current_timeline()
        if timeline is None:
            return {"success": False, "error": "No active timeline"}

        info = self._timeline_rate_info(timeline)
        fps = info["fps"]
        tl_start = info["start_frame"]

        # Normalize, merge overlapping, and sort REVERSE chronological
        merged = self._normalize_time_segments(cut_segments, merge_gap=0.04)
        if not merged:
            return {"success": True, "cuts_applied": 0, "message": "No valid cut segments"}

        merged.sort(key=lambda s: s["start"], reverse=True)

        # Save current playhead to restore later
        saved_tc = timeline.GetCurrentTimecode()

        applied = 0
        total_removed_seconds = 0.0

        for seg in merged:
            start_sec = float(seg["start"])
            end_sec = float(seg["end"])
            if end_sec <= start_sec:
                continue

            start_frame = seg.get("start_frame")
            end_frame = seg.get("end_frame")
            if start_frame is None:
                start_frame = tl_start + int(round(start_sec * fps))
            if end_frame is None:
                end_frame = tl_start + int(round(end_sec * fps))
            start_frame = int(start_frame)
            end_frame = int(end_frame)

            # Blade at the END point first (so it doesn't shift the start)
            self.set_playhead_frame(end_frame)
            _time.sleep(0.05)
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" '
                'to key code 11 using {command down}'  # Cmd+B = Blade
            ], capture_output=True, timeout=2)
            _time.sleep(0.05)

            # Blade at the START point
            self.set_playhead_frame(start_frame)
            _time.sleep(0.05)
            subprocess.run(["osascript", "-e",
                'tell application "System Events" to tell process "Resolve" '
                'to key code 11 using {command down}'  # Cmd+B = Blade
            ], capture_output=True, timeout=2)
            _time.sleep(0.1)  # Extra wait for Resolve to process the splits

            # Find clips that are now entirely within the cut range
            clips_to_delete = []
            for media_type in ("video", "audio"):
                track_count = timeline.GetTrackCount(media_type) or 0
                for track_idx in range(1, track_count + 1):
                    items = timeline.GetItemListInTrack(media_type, track_idx) or []
                    for item in items:
                        try:
                            cs = int(item.GetStart())
                            ce = int(item.GetEnd())
                        except Exception:
                            continue
                        # Clip must be within the cut range (with 1-frame tolerance
                        # for rounding after blade)
                        if cs >= start_frame - 1 and ce <= end_frame + 1 and cs < ce:
                            clips_to_delete.append(item)

            if clips_to_delete:
                try:
                    ok = timeline.DeleteClips(clips_to_delete, True)  # ripple=True
                    if ok:
                        applied += 1
                        total_removed_seconds += end_sec - start_sec
                    else:
                        logger.warning("DeleteClips returned False for segment %.1f-%.1f", start_sec, end_sec)
                except TypeError:
                    # Some Resolve builds don't support the ripple arg
                    try:
                        timeline.DeleteClips(clips_to_delete)
                        applied += 1
                        total_removed_seconds += end_sec - start_sec
                    except Exception as e:
                        logger.error("DeleteClips fallback failed: %s", e)
                except Exception as e:
                    logger.error("DeleteClips failed for segment %.1f-%.1f: %s", start_sec, end_sec, e)

            _time.sleep(0.05)

        # Restore playhead
        if saved_tc:
            try:
                timeline.SetCurrentTimecode(saved_tc)
            except Exception:
                pass

        # Clear in/out marks
        self.clear_in_out_range()

        return {
            "success": True,
            "cuts_applied": applied,
            "total_removed_seconds": round(total_removed_seconds, 1),
            "timeline_name": timeline.GetName(),
        }

    def get_clip_at_playhead(self, track_type: str = "video", track_index: int = 1):
        """
        Return the clip on the given track that contains the current playhead.
        Falls back to the first clip if no clip spans the playhead.
        """
        clips = self.get_timeline_clips(track_type, track_index)
        if not clips:
            return None

        timeline = self.get_current_timeline()
        if timeline is None:
            return clips[0]

        try:
            info = self._timeline_rate_info(timeline)
            current_tc = timeline.GetCurrentTimecode()
            if current_tc:
                start_tc_frames = self._timecode_to_frames(
                    info["start_tc"], info["fps"], drop_frame=info["drop_frame"]
                )
                current_tc_frames = self._timecode_to_frames(
                    current_tc, info["fps"], drop_frame=info["drop_frame"]
                )
                current_frame = info["start_frame"] + (current_tc_frames - start_tc_frames)
            else:
                current_frame = None
        except Exception:
            return clips[0]

        if current_frame is None:
            return clips[0]

        for clip in clips:
            try:
                clip_start = clip.GetStart()
                clip_end = clip.GetEnd()
                if clip_start <= current_frame < clip_end:
                    return clip
            except Exception:
                continue

        return clips[0]

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

    def get_timeline_video_clip_contexts(
        self,
        track_index: int = 1,
        in_frame: int | None = None,
        out_frame: int | None = None,
    ) -> dict:
        """
        Return clip contexts for a video track, optionally filtered to an in/out range.

        The returned context shape mirrors get_clip_audio_context() so downstream
        features can reason about timeline and source time in one format.
        """
        timeline = self.get_current_timeline()
        base_result = {
            "timeline": timeline,
            "contexts": [],
            "track_index": int(track_index),
            "clip_count": 0,
            "usable_count": 0,
            "has_any_items": False,
            "range_in_frame": int(in_frame) if in_frame is not None else None,
            "range_out_frame": int(out_frame) if out_frame is not None else None,
        }
        if timeline is None:
            return base_result

        items = self.get_timeline_clips("video", track_index)
        base_result["clip_count"] = len(items)
        base_result["has_any_items"] = bool(items)
        if not items:
            return base_result

        contexts: list[dict] = []
        for item in items:
            context = self.get_clip_audio_context(item)
            if context is None:
                continue
            clip_start_frame = int(context["clip_start_frame"])
            clip_end_frame = int(context["clip_end_frame"])
            if in_frame is not None and clip_end_frame <= int(in_frame):
                continue
            if out_frame is not None and clip_start_frame >= int(out_frame):
                continue

            context["track_type"] = "video"
            context["track_index"] = int(track_index)
            context["clip_name"] = self.get_timeline_item_name(item)
            contexts.append(context)

        base_result["contexts"] = contexts
        base_result["usable_count"] = len(contexts)
        return base_result

    def _iter_audio_extraction_track_candidates(self, timeline, preferred_track_index: int = 1):
        """Yield track candidates in a stable priority order for timeline audio extraction."""
        seen: set[tuple[str, int]] = set()

        for media_type in ("audio", "video"):
            try:
                track_count = int(timeline.GetTrackCount(media_type) or 0)
            except Exception:
                track_count = 0
            if 1 <= int(preferred_track_index) <= track_count:
                key = (media_type, int(preferred_track_index))
                if key not in seen:
                    seen.add(key)
                    yield key

        for media_type in ("audio", "video"):
            try:
                track_count = int(timeline.GetTrackCount(media_type) or 0)
            except Exception:
                track_count = 0
            for track_index in range(1, track_count + 1):
                key = (media_type, track_index)
                if key in seen:
                    continue
                seen.add(key)
                yield key

    def get_timeline_audio_clip_contexts(self, preferred_track_index: int = 1) -> dict:
        """
        Pick the best available timeline track for audio extraction and return usable clip contexts.

        Some Resolve timelines expose unusable items on audio track 1 even when video track 1
        still points at valid source media. We inspect both preferred tracks first, then any
        remaining tracks, and keep the candidate with the highest usable clip count.
        """
        timeline = self.get_current_timeline()
        base_result = {
            "timeline": timeline,
            "contexts": [],
            "attempts": [],
            "has_any_items": False,
            "selected_media_type": None,
            "selected_track_index": None,
            "clip_count": 0,
            "usable_count": 0,
        }
        if timeline is None:
            return base_result

        best_selection: dict | None = None

        for media_type, track_index in self._iter_audio_extraction_track_candidates(
            timeline,
            preferred_track_index=preferred_track_index,
        ):
            items = self.get_timeline_clips(media_type, track_index)
            clip_count = len(items)
            if clip_count == 0:
                base_result["attempts"].append({
                    "media_type": media_type,
                    "track_index": int(track_index),
                    "clip_count": 0,
                    "usable_count": 0,
                    "missing_file_path_count": 0,
                    "invalid_context_count": 0,
                })
                continue

            base_result["has_any_items"] = True
            contexts: list[dict] = []
            missing_file_path_count = 0
            invalid_context_count = 0

            for item in items:
                context = self.get_clip_audio_context(item)
                if context is not None:
                    contexts.append(context)
                    continue

                if self.get_clip_file_path(item):
                    invalid_context_count += 1
                else:
                    missing_file_path_count += 1

            attempt = {
                "media_type": media_type,
                "track_index": int(track_index),
                "clip_count": clip_count,
                "usable_count": len(contexts),
                "missing_file_path_count": missing_file_path_count,
                "invalid_context_count": invalid_context_count,
            }
            base_result["attempts"].append(attempt)

            if not contexts:
                continue

            if best_selection is None or len(contexts) > int(best_selection["usable_count"]):
                best_selection = {
                    **attempt,
                    "contexts": contexts,
                }

        if best_selection is None:
            logger.warning(
                "No usable clips found for timeline audio extraction across %d track candidates",
                len(base_result["attempts"]),
            )
            return base_result

        result = {
            **base_result,
            "contexts": best_selection["contexts"],
            "selected_media_type": best_selection["media_type"],
            "selected_track_index": best_selection["track_index"],
            "clip_count": best_selection["clip_count"],
            "usable_count": best_selection["usable_count"],
        }

        selected_key = (result["selected_media_type"], result["selected_track_index"])
        preferred_key = ("audio", int(preferred_track_index))
        if selected_key != preferred_key or int(result["usable_count"]) < int(result["clip_count"]):
            logger.info(
                "Using %s track %d for timeline audio extraction (%d/%d usable clips)",
                result["selected_media_type"],
                result["selected_track_index"],
                result["usable_count"],
                result["clip_count"],
            )

        return result

    def _pick_audio_render_format_and_codec(self, project) -> tuple[str, str, str | None] | None:
        """Choose a render format/codec combination that can produce an audio file."""
        try:
            formats = project.GetRenderFormats() or {}
        except Exception:
            formats = {}

        if not isinstance(formats, dict) or not formats:
            logger.warning("Resolve did not return any render formats for timeline audio fallback")
            return None

        preferred_formats: list[tuple[str, str]] = []
        for format_key in ("wav", "aiff", "aif", "mp3", "mov"):
            extension = formats.get(format_key)
            if extension:
                preferred_formats.append((str(format_key), str(extension).lstrip(".")))

        if not preferred_formats:
            first_format, first_extension = next(iter(formats.items()))
            preferred_formats.append((str(first_format), str(first_extension).lstrip(".")))

        for format_key, extension in preferred_formats:
            try:
                codecs = project.GetRenderCodecs(format_key) or {}
            except Exception:
                codecs = {}

            codec_values: list[str] = []
            if isinstance(codecs, dict):
                codec_values = [str(value) for value in codecs.values() if value]
            elif isinstance(codecs, (list, tuple, set)):
                codec_values = [str(value) for value in codecs if value]

            preferred_codecs: list[str] = []
            for codec in codec_values:
                lowered = codec.lower()
                if format_key in ("wav", "aiff", "aif") and ("pcm" in lowered or "lpcm" in lowered):
                    preferred_codecs.append(codec)
                elif format_key == "mp3" and "mp3" in lowered:
                    preferred_codecs.append(codec)

            ordered_codecs = preferred_codecs + [codec for codec in codec_values if codec not in preferred_codecs]
            if not ordered_codecs:
                ordered_codecs = [None]

            for codec in ordered_codecs:
                try:
                    if codec is None:
                        return format_key, extension, None
                    if project.SetCurrentRenderFormatAndCodec(format_key, codec):
                        return format_key, extension, codec
                except Exception:
                    continue

        logger.warning("Could not find a usable render format/codec for timeline audio fallback")
        return None

    def _load_render_preset_for_audio(self, project) -> str | None:
        """Load any available render preset so Resolve has a valid deliver baseline."""
        preset_names: list[str] = []

        for getter_name in ("GetRenderPresetList", "GetRenderPresets"):
            getter = getattr(project, getter_name, None)
            if not callable(getter):
                continue
            try:
                raw_presets = getter() or []
            except Exception:
                continue

            if isinstance(raw_presets, dict):
                preset_names = [str(value) for value in raw_presets.values() if value]
            else:
                preset_names = [str(value) for value in raw_presets if value]

            if preset_names:
                break

        if not preset_names:
            return None

        ordered_presets = [name for name in preset_names if "audio" in name.lower()]
        ordered_presets.extend(name for name in preset_names if name not in ordered_presets)

        for preset_name in ordered_presets:
            try:
                if project.LoadRenderPreset(preset_name):
                    return preset_name
            except Exception:
                continue

        return None

    def _get_current_page_name(self) -> str | None:
        """Best-effort read of Resolve's current page name."""
        resolve = self._resolve
        if resolve is None:
            return None

        getter = getattr(resolve, "GetCurrentPage", None)
        if not callable(getter):
            return None

        try:
            page_name = getter()
        except Exception:
            return None

        return str(page_name) if page_name else None

    def _open_resolve_page(self, page_name: str) -> bool:
        """Best-effort page switch in Resolve."""
        resolve = self._resolve
        if resolve is None:
            return False

        opener = getattr(resolve, "OpenPage", None)
        if not callable(opener):
            return False

        try:
            result = opener(page_name)
        except Exception:
            return False

        return bool(result is None or result)

    def _restore_resolve_page(self, page_name: str | None, retries: int = 3, delay_seconds: float = 0.15) -> None:
        """Return Resolve to the user's original page after temporary Deliver renders."""
        if not page_name:
            return

        current_page = self._get_current_page_name()
        if current_page == page_name:
            return

        for attempt in range(max(retries, 1)):
            if self._open_resolve_page(page_name):
                current_page = self._get_current_page_name()
                if current_page is None or current_page == page_name:
                    return
            if attempt < retries - 1:
                time.sleep(delay_seconds)

        logger.warning("Failed to restore Resolve page to %s after timeline audio render", page_name)

    def render_current_timeline_audio(
        self,
        name_prefix: str = "finlaycut_timeline_audio",
        mark_in_frame: int | None = None,
        mark_out_frame: int | None = None,
        fallback_restore_page: str | None = None,
    ) -> str | None:
        """
        Render the current timeline's audible mix to a temp file.

        This is a fallback for timelines whose clips do not expose source file paths via
        Resolve's timeline item API, such as nested or compound timeline items.
        """
        if not self.connected:
            return None

        project = self.get_current_project()
        timeline = self.get_current_timeline()
        if project is None or timeline is None:
            return None

        render_job_id = None
        original_mode = None
        original_format = None
        original_page = self._get_current_page_name()
        restore_page = original_page or fallback_restore_page

        try:
            try:
                original_mode = project.GetCurrentRenderMode()
            except Exception:
                original_mode = None

            try:
                original_format = project.GetCurrentRenderFormatAndCodec()
            except Exception:
                original_format = None

            self._open_resolve_page("deliver")

            loaded_preset = self._load_render_preset_for_audio(project)
            if loaded_preset:
                logger.info("Loaded render preset for timeline audio fallback: %s", loaded_preset)

            selection = self._pick_audio_render_format_and_codec(project)
            if selection is None:
                return None

            format_key, extension, codec = selection
            logger.info(
                "Using render format for timeline audio fallback: %s/%s",
                format_key,
                codec or "default",
            )
            if codec is not None:
                project.SetCurrentRenderFormatAndCodec(format_key, codec)

            output_dir = settings.temp_dir / "resolve-renders"
            output_dir.mkdir(parents=True, exist_ok=True)
            render_name = f"{name_prefix}_{int(time.time() * 1000)}"
            expected_output = Path(output_dir) / f"{render_name}.{extension}"

            if original_mode is not None:
                try:
                    project.SetCurrentRenderMode(1)
                except Exception:
                    pass

            render_settings = {
                "TargetDir": str(output_dir),
                "CustomName": render_name,
            }
            if mark_in_frame is not None and mark_out_frame is not None and int(mark_out_frame) >= int(mark_in_frame):
                render_settings["MarkIn"] = int(mark_in_frame)
                render_settings["MarkOut"] = int(mark_out_frame)
            else:
                render_settings["SelectAllFrames"] = 1
            if format_key in {"mov", "mp4", "mxf"}:
                render_settings["ExportVideo"] = 0
                render_settings["ExportAudio"] = 1

            if not project.SetRenderSettings(render_settings):
                minimal_settings = {
                    "TargetDir": str(output_dir),
                    "CustomName": render_name,
                }
                if not project.SetRenderSettings(minimal_settings):
                    logger.warning(
                        "Resolve rejected timeline audio render settings for format=%s codec=%s: %s",
                        format_key,
                        codec or "default",
                        render_settings,
                    )
                    return None

            render_job_id = project.AddRenderJob()
            if not render_job_id:
                logger.warning("Resolve did not create a render job for timeline audio fallback")
                return None

            if not project.StartRendering(render_job_id):
                logger.warning("Resolve refused to start timeline audio render job %s", render_job_id)
                return None

            deadline = time.time() + 180.0
            while project.IsRenderingInProgress():
                if time.time() >= deadline:
                    try:
                        project.StopRendering()
                    except Exception:
                        pass
                    logger.warning("Timed out waiting for timeline audio render job %s", render_job_id)
                    return None
                time.sleep(0.25)

            status = project.GetRenderJobStatus(render_job_id) or {}
            candidate_paths = [expected_output]
            candidate_paths.extend(sorted(output_dir.glob(f"{render_name}*")))
            for candidate in candidate_paths:
                try:
                    if candidate.is_file() and candidate.stat().st_size > 0:
                        logger.info(
                            "Rendered timeline audio fallback using %s/%s -> %s",
                            format_key,
                            codec or "default",
                            candidate,
                        )
                        return str(candidate)
                except Exception:
                    continue

            logger.warning("Timeline audio render finished without an output file: %s", status)
            return None
        except Exception as exc:
            logger.warning("Timeline audio render fallback failed: %s", exc)
            return None
        finally:
            if render_job_id:
                try:
                    project.DeleteRenderJob(render_job_id)
                except Exception:
                    pass

            if original_mode is not None:
                try:
                    project.SetCurrentRenderMode(original_mode)
                except Exception:
                    pass

            if isinstance(original_format, dict):
                format_name = original_format.get("format")
                codec_name = original_format.get("codec")
                if format_name and codec_name:
                    try:
                        project.SetCurrentRenderFormatAndCodec(str(format_name), str(codec_name))
                    except Exception:
                        pass

            self._restore_resolve_page(restore_page)

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

    def get_clip_audio_context(self, timeline_item) -> dict | None:
        """
        Return a normalized timing/source context for a timeline item.
        This is the canonical mapping between timeline time and source media time.
        """
        if timeline_item is None:
            return None

        timeline = self.get_current_timeline()
        if timeline is None:
            return None

        file_path = self.get_clip_file_path(timeline_item)
        if not file_path:
            return None

        try:
            clip_start_frame = int(timeline_item.GetStart())
            clip_end_frame = int(timeline_item.GetEnd())
        except Exception:
            return None

        if clip_end_frame <= clip_start_frame:
            return None

        info = self._timeline_rate_info(timeline)
        fps = float(info["fps"])
        timeline_start_frame = int(info["start_frame"])
        source_range = self.get_timeline_item_source_range(timeline_item)

        context = {
            "file_path": file_path,
            "fps": fps,
            "timeline_start_frame": timeline_start_frame,
            "clip_start_frame": clip_start_frame,
            "clip_end_frame": clip_end_frame,
            "clip_duration_frames": int(clip_end_frame - clip_start_frame),
            "clip_duration_seconds": (clip_end_frame - clip_start_frame) / fps,
            "clip_timeline_offset": (clip_start_frame - timeline_start_frame) / fps,
            "clip_item_id": self.get_timeline_item_unique_id(timeline_item),
        }

        if source_range is not None:
            context.update({
                "source_start_frame": int(source_range["source_start_frame"]),
                "source_end_frame": int(source_range["source_end_frame"]),
                "source_start_seconds": int(source_range["source_start_frame"]) / fps,
                "source_end_seconds": int(source_range["source_end_frame"]) / fps,
                "source_strategy": source_range.get("strategy"),
            })
        else:
            context.update({
                "source_start_frame": None,
                "source_end_frame": None,
                "source_start_seconds": None,
                "source_end_seconds": None,
                "source_strategy": None,
            })

        return context

    def _call_timeline_item_int_method(self, timeline_item, method_name: str) -> int | None:
        method = getattr(timeline_item, method_name, None)
        if not callable(method):
            return None
        try:
            value = method()
        except Exception:
            return None
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _call_timeline_item_str_method(self, timeline_item, method_name: str) -> str | None:
        method = getattr(timeline_item, method_name, None)
        if not callable(method):
            return None
        try:
            value = method()
        except Exception:
            return None
        if value is None:
            return None
        value_str = str(value).strip()
        return value_str or None

    def get_timeline_item_name(self, timeline_item) -> str:
        """Return the best available display name for a timeline item."""
        for method_name in ("GetName", "GetClipName"):
            value = self._call_timeline_item_str_method(timeline_item, method_name)
            if value:
                return value
        try:
            media_pool_item = timeline_item.GetMediaPoolItem()
        except Exception:
            media_pool_item = None
        if media_pool_item is not None:
            for method_name in ("GetName",):
                method = getattr(media_pool_item, method_name, None)
                if callable(method):
                    try:
                        value = method()
                    except Exception:
                        value = None
                    if value:
                        return str(value)
            try:
                props = media_pool_item.GetClipProperty()
            except Exception:
                props = None
            if isinstance(props, dict):
                for key in ("Clip Name", "File Name"):
                    value = props.get(key)
                    if value:
                        return str(value)
        file_path = self.get_clip_file_path(timeline_item)
        if file_path:
            return Path(file_path).name
        return "Clip"

    def get_timeline_item_unique_id(self, timeline_item) -> str | None:
        """Return a stable Resolve unique ID for a timeline item if available."""
        for method_name in ("GetUniqueId", "GetUUID"):
            value = self._call_timeline_item_str_method(timeline_item, method_name)
            if value:
                return value
        return None

    def get_timeline_item_source_range(self, timeline_item) -> dict | None:
        """
        Resolve the source in/out frame range used by a timeline item.
        Prefer explicit source frame APIs when available; fall back to left-offset math.
        """
        if timeline_item is None:
            return None

        try:
            clip_start = int(timeline_item.GetStart())
            clip_end = int(timeline_item.GetEnd())
        except Exception:
            return None

        clip_duration = max(0, clip_end - clip_start)
        if clip_duration <= 0:
            return None

        start_methods = (
            "GetSourceStartFrame",
            "GetSourceStart",
            "GetSourceInFrame",
            "GetSourceIn",
        )
        end_methods = (
            "GetSourceEndFrame",
            "GetSourceEnd",
            "GetSourceOutFrame",
            "GetSourceOut",
        )

        source_start_frame = None
        source_end_frame = None
        strategy = None

        for method_name in start_methods:
            value = self._call_timeline_item_int_method(timeline_item, method_name)
            if value is not None and value >= 0:
                source_start_frame = value
                strategy = method_name
                break

        for method_name in end_methods:
            value = self._call_timeline_item_int_method(timeline_item, method_name)
            if value is not None and value > 0:
                source_end_frame = value
                strategy = strategy or method_name
                break

        if source_start_frame is not None:
            if source_end_frame is None:
                source_end_frame = source_start_frame + clip_duration
            elif source_end_frame <= source_start_frame:
                source_end_frame = source_start_frame + clip_duration

            # Some APIs may report inclusive end frames; normalize if so.
            source_span = source_end_frame - source_start_frame
            if source_span == clip_duration - 1:
                source_end_frame += 1

            if source_end_frame > source_start_frame:
                return {
                    "source_start_frame": int(source_start_frame),
                    "source_end_frame": int(source_end_frame),
                    "clip_duration_frames": int(clip_duration),
                    "strategy": strategy or "source_method",
                }

        try:
            left_offset = int(timeline_item.GetLeftOffset())
        except Exception:
            left_offset = None

        if left_offset is not None and left_offset >= 0:
            return {
                "source_start_frame": int(left_offset),
                "source_end_frame": int(left_offset + clip_duration),
                "clip_duration_frames": int(clip_duration),
                "strategy": "left_offset_fallback",
            }

        return None

    def find_timeline_item_by_unique_id(
        self,
        unique_id: str,
        media_types: tuple[str, ...] = ("audio", "video"),
    ) -> dict | None:
        """Find a timeline item by Resolve unique ID."""
        if not unique_id:
            return None

        timeline = self.get_current_timeline()
        if timeline is None:
            return None

        for media_type in media_types:
            track_count = timeline.GetTrackCount(media_type) or 0
            for track_idx in range(1, track_count + 1):
                items = timeline.GetItemListInTrack(media_type, track_idx) or []
                for item in items:
                    if self.get_timeline_item_unique_id(item) == unique_id:
                        return {
                            "item": item,
                            "media_type": media_type,
                            "track_index": track_idx,
                        }

        return None

    def _find_timeline_item_by_source_position(
        self,
        file_path: str | None,
        source_frame: int | None,
        media_types: tuple[str, ...] = ("audio", "video"),
    ) -> dict | None:
        """Find a timeline item whose source span contains the given source frame."""
        if not file_path or source_frame is None:
            return None

        timeline = self.get_current_timeline()
        if timeline is None:
            return None

        matches = []
        for media_type in media_types:
            track_count = timeline.GetTrackCount(media_type) or 0
            for track_idx in range(1, track_count + 1):
                items = timeline.GetItemListInTrack(media_type, track_idx) or []
                for item in items:
                    item_file_path = self.get_clip_file_path(item)
                    if item_file_path != file_path:
                        continue
                    source_range = self.get_timeline_item_source_range(item)
                    if source_range is None:
                        continue
                    if source_range["source_start_frame"] <= int(source_frame) < source_range["source_end_frame"]:
                        matches.append({
                            "item": item,
                            "media_type": media_type,
                            "track_index": track_idx,
                            "source_range": source_range,
                        })

        if not matches:
            return None

        matches.sort(key=lambda m: (0 if m["media_type"] == "audio" else 1, m["track_index"]))
        return matches[0]

    def _get_timeline_item_records(
        self,
        media_types: tuple[str, ...] = ("audio", "video"),
    ) -> list[dict]:
        """Collect normalized timeline item metadata for signature and transcript sync."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return []

        records: list[dict] = []
        for media_type in media_types:
            track_count = timeline.GetTrackCount(media_type) or 0
            for track_idx in range(1, track_count + 1):
                items = timeline.GetItemListInTrack(media_type, track_idx) or []
                for item in items:
                    try:
                        clip_start_frame = int(item.GetStart())
                        clip_end_frame = int(item.GetEnd())
                    except Exception:
                        continue

                    if clip_end_frame <= clip_start_frame:
                        continue

                    source_range = self.get_timeline_item_source_range(item)
                    record = {
                        "item": item,
                        "media_type": media_type,
                        "track_index": int(track_idx),
                        "timeline_item_unique_id": self.get_timeline_item_unique_id(item),
                        "file_path": self.get_clip_file_path(item),
                        "clip_start_frame": int(clip_start_frame),
                        "clip_end_frame": int(clip_end_frame),
                        "source_start_frame": None,
                        "source_end_frame": None,
                    }
                    if source_range is not None:
                        record["source_start_frame"] = int(source_range["source_start_frame"])
                        record["source_end_frame"] = int(source_range["source_end_frame"])

                    records.append(record)

        return records

    def _timeline_record_is_retimed(self, record: dict) -> bool:
        """Best-effort retime detection: source span should match clip span for transcript-safe sync."""
        source_start_frame = record.get("source_start_frame")
        source_end_frame = record.get("source_end_frame")
        if source_start_frame is None or source_end_frame is None:
            return False

        try:
            source_span = int(source_end_frame) - int(source_start_frame)
            clip_span = int(record["clip_end_frame"]) - int(record["clip_start_frame"])
        except Exception:
            return False

        return source_span > 0 and clip_span > 0 and source_span != clip_span

    def _timeline_record_priority(self, record: dict) -> tuple[int, int, int]:
        media_type = str(record.get("media_type") or "")
        media_rank = 0 if media_type == "audio" else 1 if media_type == "video" else 2
        has_source_range = 0 if record.get("source_start_frame") is not None and record.get("source_end_frame") is not None else 1
        return (
            media_rank,
            has_source_range,
            int(record.get("track_index") or 0),
        )

    def _timeline_record_equivalence_key(self, record: dict) -> tuple:
        return (
            str(record.get("timeline_item_unique_id") or ""),
            str(record.get("file_path") or ""),
            int(record.get("clip_start_frame") or 0),
            int(record.get("clip_end_frame") or 0),
            int(record["source_start_frame"]) if record.get("source_start_frame") is not None else None,
            int(record["source_end_frame"]) if record.get("source_end_frame") is not None else None,
        )

    def _dedupe_timeline_record_candidates(self, records: list[dict]) -> list[dict]:
        canonical: dict[tuple, dict] = {}
        for record in records:
            key = self._timeline_record_equivalence_key(record)
            current = canonical.get(key)
            if current is None or self._timeline_record_priority(record) < self._timeline_record_priority(current):
                canonical[key] = record
        return list(canonical.values())

    def _resolve_word_frames_for_record(
        self,
        record: dict,
        source_start_frame: int | None,
        source_end_frame: int | None,
        cached_start_frame: int | None,
        cached_end_frame: int | None,
        original_clip_start_frame: int | None,
    ) -> tuple[int, int] | None:
        try:
            record_clip_start = int(record["clip_start_frame"])
            record_clip_end = int(record["clip_end_frame"])
        except Exception:
            return None

        record_source_start = record.get("source_start_frame")
        record_source_end = record.get("source_end_frame")

        if (
            source_start_frame is not None
            and source_end_frame is not None
            and record_source_start is not None
            and record_source_end is not None
        ):
            resolved_start_frame = record_clip_start + (int(source_start_frame) - int(record_source_start))
            resolved_end_frame = record_clip_start + (int(source_end_frame) - int(record_source_start))
        else:
            if cached_start_frame is None or original_clip_start_frame is None:
                return None
            resolved_start_frame = record_clip_start + (int(cached_start_frame) - int(original_clip_start_frame))
            cached_end_value = int(cached_end_frame if cached_end_frame is not None else cached_start_frame)
            resolved_end_frame = resolved_start_frame + max(0, cached_end_value - int(cached_start_frame))

        if resolved_end_frame <= resolved_start_frame:
            return None
        if resolved_start_frame < record_clip_start or resolved_end_frame > record_clip_end:
            return None

        return int(resolved_start_frame), int(resolved_end_frame)

    def _select_transcript_sync_candidate(
        self,
        candidates: list[dict],
        source_start_frame: int | None,
        source_end_frame: int | None,
        cached_start_frame: int | None,
        cached_end_frame: int | None,
        original_clip_start_frame: int | None,
        previous_start_frame: int | None,
    ) -> dict | None:
        deduped_candidates = self._dedupe_timeline_record_candidates(candidates)
        viable_candidates = [
            record for record in deduped_candidates
            if not self._timeline_record_is_retimed(record)
        ] or deduped_candidates

        matches: list[dict] = []
        for record in viable_candidates:
            resolved_frames = self._resolve_word_frames_for_record(
                record,
                source_start_frame,
                source_end_frame,
                cached_start_frame,
                cached_end_frame,
                original_clip_start_frame,
            )
            if resolved_frames is None:
                continue

            resolved_start_frame, resolved_end_frame = resolved_frames
            if previous_start_frame is not None and resolved_start_frame + 1 < previous_start_frame:
                continue

            timeline_distance = (
                abs(resolved_start_frame - int(cached_start_frame))
                if cached_start_frame is not None
                else 0
            )
            clip_distance = (
                abs(int(record["clip_start_frame"]) - int(original_clip_start_frame))
                if original_clip_start_frame is not None
                else 0
            )
            matches.append({
                "record": record,
                "resolved_start_frame": resolved_start_frame,
                "resolved_end_frame": resolved_end_frame,
                "sort_key": (
                    int(timeline_distance),
                    int(clip_distance),
                    self._timeline_record_priority(record),
                    int(resolved_start_frame),
                ),
            })

        if not matches:
            return None

        matches.sort(key=lambda match: match["sort_key"])
        if len(matches) > 1 and matches[0]["sort_key"] == matches[1]["sort_key"]:
            return None
        return matches[0]

    def get_timeline_signature(self) -> dict | None:
        """Return a deterministic fingerprint for the current timeline contents."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return None

        try:
            timeline_name = timeline.GetName()
            timeline_start_frame = int(timeline.GetStartFrame())
            timeline_end_frame = int(timeline.GetEndFrame())
            video_tracks = int(timeline.GetTrackCount("video") or 0)
            audio_tracks = int(timeline.GetTrackCount("audio") or 0)
        except Exception as e:
            logger.error("Failed to read timeline signature metadata: %s", e)
            return None

        records = self._get_timeline_item_records()
        signature_items = [
            {
                "media_type": record["media_type"],
                "track_index": int(record["track_index"]),
                "timeline_item_unique_id": record.get("timeline_item_unique_id"),
                "file_path": record.get("file_path"),
                "clip_start_frame": int(record["clip_start_frame"]),
                "clip_end_frame": int(record["clip_end_frame"]),
                "source_start_frame": record.get("source_start_frame"),
                "source_end_frame": record.get("source_end_frame"),
            }
            for record in records
        ]
        signature_items.sort(
            key=lambda item: (
                item["media_type"],
                int(item["track_index"]),
                int(item["clip_start_frame"]),
                int(item["clip_end_frame"]),
                str(item.get("file_path") or ""),
                str(item.get("timeline_item_unique_id") or ""),
            )
        )

        payload = {
            "timeline_name": timeline_name,
            "timeline_start_frame": timeline_start_frame,
            "timeline_end_frame": timeline_end_frame,
            "video_track_count": video_tracks,
            "audio_track_count": audio_tracks,
            "items": signature_items,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

        return {
            "timeline_name": timeline_name,
            "timeline_signature": digest,
            "start_frame": timeline_start_frame,
            "end_frame": timeline_end_frame,
            "video_tracks": video_tracks,
            "audio_tracks": audio_tracks,
            "item_count": len(signature_items),
        }

    def resolve_transcript_words_on_current_timeline(self, words: list[dict], context: dict | None = None) -> dict:
        """
        Re-anchor transcript words onto the current timeline using unique IDs and source ranges.
        This is intentionally strict: unsafe or ambiguous matches require a re-transcribe.
        """
        timeline = self.get_current_timeline()
        if timeline is None:
            return {
                "success": False,
                "reason": "content_changed",
                "error": "No active timeline",
                "coverage_ratio": 0.0,
                "confidence": 0.0,
            }

        context = context or {}
        info = self._timeline_rate_info(timeline)
        fps = float(info["fps"])
        timeline_start_frame = int(info["start_frame"])
        records = self._get_timeline_item_records()
        if not records:
            return {
                "success": False,
                "reason": "content_changed",
                "error": "No timeline items found on the current timeline",
                "coverage_ratio": 0.0,
                "confidence": 0.0,
            }

        unique_index: dict[str, list[dict]] = {}
        file_index: dict[str, list[dict]] = {}
        for record in records:
            unique_id = record.get("timeline_item_unique_id")
            if unique_id:
                unique_index.setdefault(str(unique_id), []).append(record)
            file_path = record.get("file_path")
            if file_path:
                file_index.setdefault(str(file_path), []).append(record)

        total_duration = 0.0
        matched_duration = 0.0
        resolved_words: list[dict] = []
        previous_start_frame: int | None = None
        unique_clip_ids: set[str] = set()
        unique_file_paths: set[str] = set()

        def _failure(reason: str, error: str) -> dict:
            coverage_ratio = matched_duration / total_duration if total_duration > 0 else 0.0
            return {
                "success": False,
                "reason": reason,
                "error": error,
                "coverage_ratio": round(coverage_ratio, 4),
                "confidence": round(max(0.0, coverage_ratio - 0.05), 4),
                "matched_words": len(resolved_words),
                "total_words": len(words),
            }

        for word in words:
            duration = max(0.0, float(word.get("end", 0.0)) - float(word.get("start", 0.0)))
            total_duration += duration

            clip_item_id = word.get("clip_item_id") or context.get("clip_item_id")
            file_path = word.get("file_path") or context.get("file_path")
            source_start_frame = word.get("source_start_frame")
            source_end_frame = word.get("source_end_frame")
            cached_start_frame = word.get("start_frame")
            cached_end_frame = word.get("end_frame", cached_start_frame)
            original_clip_start_frame = context.get("clip_start_frame")

            candidates: list[dict] = []
            if clip_item_id:
                candidates = list(unique_index.get(str(clip_item_id), []))

            if not candidates and file_path and source_start_frame is not None:
                for record in file_index.get(str(file_path), []):
                    record_source_start = record.get("source_start_frame")
                    record_source_end = record.get("source_end_frame")
                    if record_source_start is None or record_source_end is None:
                        continue
                    if not (int(record_source_start) <= int(source_start_frame) < int(record_source_end)):
                        continue
                    if source_end_frame is not None and int(source_end_frame) > int(record_source_end):
                        continue
                    candidates.append(record)

            if not candidates:
                return _failure(
                    "content_changed",
                    "The current timeline no longer contains the clip content needed for this transcript.",
                )

            candidate_match = self._select_transcript_sync_candidate(
                candidates,
                int(source_start_frame) if source_start_frame is not None else None,
                int(source_end_frame) if source_end_frame is not None else None,
                int(cached_start_frame) if cached_start_frame is not None else None,
                int(cached_end_frame) if cached_end_frame is not None else None,
                int(original_clip_start_frame) if original_clip_start_frame is not None else None,
                previous_start_frame,
            )
            if candidate_match is None:
                return _failure(
                    "ambiguous_sync",
                    "The current timeline has multiple possible matches for part of this transcript.",
                )

            record = candidate_match["record"]
            if self._timeline_record_is_retimed(record):
                return _failure(
                    "content_changed",
                    "The current timeline appears to have retimed clips, so this transcript must be re-transcribed.",
                )

            record_clip_start = int(record["clip_start_frame"])
            record_clip_end = int(record["clip_end_frame"])
            resolved_start_frame = int(candidate_match["resolved_start_frame"])
            resolved_end_frame = int(candidate_match["resolved_end_frame"])

            if resolved_end_frame <= resolved_start_frame:
                return _failure(
                    "content_changed",
                    "The current timeline trimmed through transcript-covered words and can no longer be synced safely.",
                )

            if resolved_start_frame < record_clip_start or resolved_end_frame > record_clip_end:
                return _failure(
                    "content_changed",
                    "The current timeline trimmed through transcript-covered words and requires a fresh transcription.",
                )

            if previous_start_frame is not None and resolved_start_frame + 1 < previous_start_frame:
                return _failure(
                    "ambiguous_sync",
                    "The synced transcript would no longer preserve word order on the current timeline.",
                )
            previous_start_frame = resolved_start_frame

            unique_id = record.get("timeline_item_unique_id")
            if unique_id:
                unique_clip_ids.add(str(unique_id))
            if record.get("file_path"):
                unique_file_paths.add(str(record["file_path"]))

            next_word = dict(word)
            next_word.update({
                "start_frame": int(resolved_start_frame),
                "end_frame": int(resolved_end_frame),
                "start": round((int(resolved_start_frame) - timeline_start_frame) / fps, 4),
                "end": round((int(resolved_end_frame) - timeline_start_frame) / fps, 4),
                "clip_item_id": unique_id,
                "file_path": record.get("file_path"),
            })
            resolved_words.append(next_word)
            matched_duration += duration

        coverage_ratio = matched_duration / total_duration if total_duration > 0 else 1.0
        if coverage_ratio < 0.98:
            return _failure(
                "content_changed",
                "Too much transcript coverage was lost while syncing to the current timeline.",
            )

        resolved_context = {
            "clip_item_id": next(iter(unique_clip_ids)) if len(unique_clip_ids) == 1 else None,
            "file_path": next(iter(unique_file_paths)) if len(unique_file_paths) == 1 else None,
            "clip_start_frame": timeline_start_frame,
            "clip_end_frame": int(timeline.GetEndFrame()),
            "source_start_frame": None,
            "source_end_frame": None,
        }

        return {
            "success": True,
            "words": resolved_words,
            "transcript_context": resolved_context,
            "coverage_ratio": round(coverage_ratio, 4),
            "confidence": round(min(1.0, coverage_ratio), 4),
            "matched_words": len(resolved_words),
            "total_words": len(words),
        }

    def resolve_transcript_word_timeline_frame(self, word: dict, context: dict | None = None) -> dict:
        """
        Resolve a transcript word back to the current timeline position, even if its clip moved.
        Prefers the original timeline item unique ID, then falls back to file path + source frame.
        """
        context = context or {}

        source_start_frame = word.get("source_start_frame")
        source_end_frame = word.get("source_end_frame")
        cached_start_frame = word.get("start_frame")
        cached_end_frame = word.get("end_frame")
        original_clip_start_frame = context.get("clip_start_frame")
        file_path = word.get("file_path") or context.get("file_path")
        clip_item_id = word.get("clip_item_id") or context.get("clip_item_id")

        match = None
        strategy = None
        if clip_item_id:
            match = self.find_timeline_item_by_unique_id(str(clip_item_id))
            if match is not None:
                strategy = "timeline_item_unique_id"

        if match is None:
            match = self._find_timeline_item_by_source_position(file_path, source_start_frame)
            if match is not None:
                strategy = "file_path_source_frame"

        if match is None:
            if cached_start_frame is None:
                return {"success": False, "error": "Could not resolve transcript clip on current timeline"}
            return {
                "success": True,
                "start_frame": int(cached_start_frame),
                "end_frame": int(cached_end_frame if cached_end_frame is not None else cached_start_frame),
                "strategy": "cached_frame_fallback",
            }

        item = match["item"]
        source_range = match.get("source_range") or self.get_timeline_item_source_range(item)
        clip_start = int(item.GetStart())

        if source_range is not None and source_start_frame is not None:
            resolved_start = clip_start + (int(source_start_frame) - int(source_range["source_start_frame"]))
            if source_end_frame is not None:
                resolved_end = clip_start + (int(source_end_frame) - int(source_range["source_start_frame"]))
            else:
                resolved_end = resolved_start
            return {
                "success": True,
                "start_frame": int(resolved_start),
                "end_frame": int(max(resolved_start, resolved_end)),
                "strategy": strategy or "source_range",
                "clip_item_id": self.get_timeline_item_unique_id(item),
            }

        if cached_start_frame is not None and original_clip_start_frame is not None:
            frame_offset = int(cached_start_frame) - int(original_clip_start_frame)
            resolved_start = clip_start + frame_offset
            if cached_end_frame is not None:
                resolved_end = resolved_start + (int(cached_end_frame) - int(cached_start_frame))
            else:
                resolved_end = resolved_start
            return {
                "success": True,
                "start_frame": int(resolved_start),
                "end_frame": int(max(resolved_start, resolved_end)),
                "strategy": f"{strategy or 'unique_id'}_clip_offset_fallback",
                "clip_item_id": self.get_timeline_item_unique_id(item),
            }

        return {"success": False, "error": "Could not compute current timeline position for transcript word"}

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
            logger.error("Error getting timeline info: %s", e)
            return None

    def _managed_marker_note_prefix(self, namespace: str) -> str:
        return f"[{namespace}]"

    def _managed_marker_custom_data_prefix(self, namespace: str) -> str:
        return f"{namespace}:"

    def _managed_marker_note_token(self, namespace: str) -> str:
        label = namespace.split(":", 1)[-1].replace("-", " ").strip() or "marker preview"
        return f"\n\nCreated by FinlayCut {label}"

    def _get_markers_on_target(self, target) -> list[dict]:
        """Return markers from a timeline or timeline item in a normalized format."""
        if target is None:
            return []

        get_markers = getattr(target, "GetMarkers", None)
        if not callable(get_markers):
            return []

        try:
            raw_markers = get_markers() or {}
        except Exception as e:
            logger.warning("Failed to read markers from Resolve target: %s", e)
            return []

        markers: list[dict] = []
        if not isinstance(raw_markers, dict):
            return markers

        for frame_key, marker in raw_markers.items():
            try:
                frame_id = int(frame_key)
            except Exception:
                continue

            data = marker if isinstance(marker, dict) else {}
            try:
                duration = int(data.get("duration") or 1)
            except Exception:
                duration = 1

            markers.append(
                {
                    "frame_id": frame_id,
                    "color": str(data.get("color") or ""),
                    "name": str(data.get("name") or ""),
                    "note": str(data.get("note") or ""),
                    "duration": max(1, duration),
                    "custom_data": str(data.get("customData") or data.get("custom_data") or ""),
                }
            )

        markers.sort(key=lambda marker: marker["frame_id"])
        return markers

    def get_timeline_markers(self) -> list[dict]:
        """Return timeline markers in a normalized format."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return []
        return self._get_markers_on_target(timeline)

    def _marker_matches_namespace(self, marker: dict, namespace: str) -> bool:
        custom_data_prefix = self._managed_marker_custom_data_prefix(namespace)
        note_prefix = self._managed_marker_note_prefix(namespace)
        note_token = self._managed_marker_note_token(namespace)
        note = str(marker.get("note") or "")
        custom_data = str(marker.get("custom_data") or "")
        return (
            custom_data.startswith(custom_data_prefix)
            or note.startswith(note_prefix)
            or note.endswith(note_token)
        )

    def _clear_managed_markers_on_target(self, target, namespace: str) -> dict:
        """Delete FinlayCut-managed markers from a timeline or timeline item."""
        if target is None:
            return {"success": False, "error": "No active marker target"}

        managed_frames = [
            int(marker["frame_id"])
            for marker in self._get_markers_on_target(target)
            if self._marker_matches_namespace(marker, namespace)
        ]
        if not managed_frames:
            return {"success": True, "deleted_count": 0}

        delete_marker_at_frame = getattr(target, "DeleteMarkerAtFrame", None)
        if not callable(delete_marker_at_frame):
            return {"success": False, "error": "Resolve does not expose marker deletion through the scripting API"}

        deleted = 0
        for frame_id in managed_frames:
            try:
                if delete_marker_at_frame(int(frame_id)):
                    deleted += 1
            except Exception as e:
                logger.warning("Failed to delete marker at frame %s: %s", frame_id, e)

        return {"success": deleted == len(managed_frames), "deleted_count": deleted}

    def clear_managed_timeline_markers(self, namespace: str) -> dict:
        """Delete only timeline markers created by FinlayCut within the given namespace."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return {"success": False, "error": "No active timeline"}
        return self._clear_managed_markers_on_target(timeline, namespace)

    def clear_managed_timeline_item_markers(
        self,
        namespace: str,
        media_types: tuple[str, ...] = ("video", "audio"),
    ) -> dict:
        """Delete FinlayCut-managed markers from timeline items across the current timeline."""
        records = self._get_timeline_item_records(media_types=media_types)
        if not records:
            return {"success": True, "deleted_count": 0}

        deleted_total = 0
        seen_items: set[int] = set()
        for record in records:
            item = record.get("item")
            if item is None:
                continue
            item_key = id(item)
            if item_key in seen_items:
                continue
            seen_items.add(item_key)

            result = self._clear_managed_markers_on_target(item, namespace)
            if result.get("error"):
                continue
            deleted_total += int(result.get("deleted_count") or 0)

        return {"success": True, "deleted_count": deleted_total}

    def add_marker(
        self,
        frame_id: int,
        color: str,
        name: str,
        note: str = "",
        duration: int = 1,
        custom_data: str = "",
    ) -> bool:
        """Add a marker to the current timeline."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return False
        try:
            normalized_duration = max(1, int(duration))
            if custom_data:
                try:
                    return bool(timeline.AddMarker(frame_id, color, name, note, normalized_duration, custom_data))
                except Exception:
                    pass
            return bool(timeline.AddMarker(frame_id, color, name, note, normalized_duration))
        except Exception:
            return False

    def _apply_managed_markers_to_target(self, target, namespace: str, markers: list[dict]) -> dict:
        if target is None:
            return {"success": False, "error": "No active marker target"}

        note_token = self._managed_marker_note_token(namespace)
        custom_data_prefix = self._managed_marker_custom_data_prefix(namespace)
        applied = 0
        failed_frames: list[int] = []

        for marker in markers:
            try:
                frame_id = int(marker["frame_id"])
            except Exception:
                continue

            try:
                duration = max(1, int(marker.get("duration") or 1))
            except Exception:
                duration = 1

            name = str(marker.get("name") or "FinlayCut Marker").strip() or "FinlayCut Marker"
            note = str(marker.get("note") or "").strip()
            fallback_note = note
            if note_token not in fallback_note:
                fallback_note = f"{fallback_note}{note_token}" if fallback_note else note_token.strip()

            raw_custom_data = str(marker.get("custom_data") or "").strip()
            custom_data = raw_custom_data if raw_custom_data.startswith(custom_data_prefix) else (
                f"{custom_data_prefix}{raw_custom_data or frame_id}"
            )
            color = str(marker.get("color") or "Yellow")

            success = False
            if custom_data:
                try:
                    success = bool(target.AddMarker(frame_id, color, name, note, duration, custom_data))
                except Exception:
                    success = False
            if not success:
                try:
                    success = bool(target.AddMarker(frame_id, color, name, fallback_note, duration))
                except Exception:
                    success = False

            if success:
                applied += 1
                continue
            failed_frames.append(frame_id)

        return {
            "success": not failed_frames,
            "applied_count": applied,
            "failed_frames": failed_frames,
        }

    def refresh_managed_timeline_markers(self, namespace: str, markers: list[dict]) -> dict:
        """Replace FinlayCut-managed timeline markers inside a namespace."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return {"success": False, "error": "No active timeline"}

        clear_result = self.clear_managed_timeline_markers(namespace)
        if clear_result.get("error"):
            return clear_result
        apply_result = self._apply_managed_markers_to_target(timeline, namespace, markers)

        return {
            "success": not apply_result.get("failed_frames"),
            "deleted_count": int(clear_result.get("deleted_count") or 0),
            "applied_count": int(apply_result.get("applied_count") or 0),
            "failed_frames": list(apply_result.get("failed_frames") or []),
        }

    def refresh_managed_markers(
        self,
        namespace: str,
        timeline_markers: list[dict] | None = None,
        timeline_item_markers: list[dict] | None = None,
        clear_item_media_types: tuple[str, ...] = ("video", "audio"),
    ) -> dict:
        """Refresh FinlayCut-managed markers across timeline and timeline items."""
        timeline = self.get_current_timeline()
        if timeline is None:
            return {"success": False, "error": "No active timeline"}

        timeline_markers = timeline_markers or []
        timeline_item_markers = timeline_item_markers or []

        timeline_clear_result = self.clear_managed_timeline_markers(namespace)
        if timeline_clear_result.get("error"):
            return timeline_clear_result

        item_clear_result = self.clear_managed_timeline_item_markers(
            namespace,
            media_types=clear_item_media_types,
        )
        if item_clear_result.get("error"):
            return item_clear_result

        timeline_apply_result = self._apply_managed_markers_to_target(
            timeline,
            namespace,
            timeline_markers,
        )

        item_applied_count = 0
        item_failed_frames: list[int] = []
        for marker in timeline_item_markers:
            item = marker.get("item")
            if item is None:
                continue
            apply_result = self._apply_managed_markers_to_target(item, namespace, [marker])
            item_applied_count += int(apply_result.get("applied_count") or 0)
            item_failed_frames.extend(int(frame) for frame in (apply_result.get("failed_frames") or []))

        return {
            "success": not timeline_apply_result.get("failed_frames") and not item_failed_frames,
            "timeline_deleted_count": int(timeline_clear_result.get("deleted_count") or 0),
            "timeline_item_deleted_count": int(item_clear_result.get("deleted_count") or 0),
            "deleted_count": int(timeline_clear_result.get("deleted_count") or 0) + int(item_clear_result.get("deleted_count") or 0),
            "timeline_applied_count": int(timeline_apply_result.get("applied_count") or 0),
            "timeline_item_applied_count": item_applied_count,
            "applied_count": int(timeline_apply_result.get("applied_count") or 0) + item_applied_count,
            "failed_frames": list(timeline_apply_result.get("failed_frames") or []),
            "timeline_item_failed_frames": item_failed_frames,
        }

    def _normalize_time_segments(self, segments: list[dict], merge_gap: float = 0.0) -> list[dict]:
        normalized: list[dict] = []
        for seg in segments:
            try:
                start = float(seg["start"])
                end = float(seg["end"])
            except Exception:
                continue
            if end <= start:
                continue
            normalized_seg = {"start": start, "end": end}
            if seg.get("start_frame") is not None and seg.get("end_frame") is not None:
                try:
                    normalized_seg["start_frame"] = int(seg["start_frame"])
                    normalized_seg["end_frame"] = int(seg["end_frame"])
                except Exception:
                    pass
            normalized.append(normalized_seg)

        if not normalized:
            return []

        normalized.sort(key=lambda s: s["start"])
        merged: list[dict] = []
        for seg in normalized:
            if not merged or seg["start"] - merged[-1]["end"] > merge_gap:
                merged.append(seg.copy())
            else:
                merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
                if seg.get("end_frame") is not None:
                    merged[-1]["end_frame"] = max(
                        int(merged[-1].get("end_frame", seg["end_frame"])),
                        int(seg["end_frame"]),
                    )
        return merged

    def _try_restore_original_clips(self, media_pool, source_clips: list[dict], tl_start: int) -> int:
        """Best-effort attempt to re-insert original clips after a failed rebuild."""
        restored = 0
        for clip in source_clips:
            try:
                ci = {
                    "mediaPoolItem": clip["media_pool_item"],
                    "startFrame": int(clip["source_start_frame"]),
                    "endFrame": int(clip["source_end_frame"]) - 1,
                    "mediaType": int(clip["media_type_code"]),
                    "trackIndex": int(clip["track_index"]),
                    "recordFrame": int(clip["clip_start"]),
                }
                result = media_pool.AppendToTimeline([ci])
                if result:
                    restored += 1
            except Exception as e:
                logger.error("Failed to restore clip: %s", e)
        self._debug(f"Restore attempt: {restored} of {len(source_clips)} clips restored")
        return restored

    def _delete_timeline_clips(self, timeline, media_types: tuple[str, ...] = ("video", "audio")) -> dict:
        clips_to_delete = []
        for media_type in media_types:
            track_count = timeline.GetTrackCount(media_type) or 0
            for track_idx in range(1, track_count + 1):
                items = timeline.GetItemListInTrack(media_type, track_idx) or []
                clips_to_delete.extend(items)

        if not clips_to_delete:
            return {"success": True, "deleted": 0}

        try:
            ok = timeline.DeleteClips(clips_to_delete, False)
        except TypeError:
            # Some Resolve builds may not expose the ripple arg.
            ok = timeline.DeleteClips(clips_to_delete)
        except Exception as e:
            return {"success": False, "error": str(e), "deleted": 0}

        if not ok:
            return {"success": False, "error": "Resolve DeleteClips returned False", "deleted": 0}

        return {"success": True, "deleted": len(clips_to_delete)}

    def _unlock_timeline_tracks(
        self,
        timeline,
        media_types: tuple[str, ...] = ("video", "audio"),
    ) -> list[tuple[str, int, bool]]:
        states: list[tuple[str, int, bool]] = []
        for media_type in media_types:
            track_count = timeline.GetTrackCount(media_type) or 0
            for track_idx in range(1, track_count + 1):
                try:
                    was_locked = bool(timeline.GetIsTrackLocked(media_type, track_idx))
                except Exception:
                    was_locked = False
                states.append((media_type, track_idx, was_locked))
                if was_locked:
                    try:
                        timeline.SetTrackLock(media_type, track_idx, False)
                    except Exception:
                        pass
        return states

    def _restore_timeline_track_locks(
        self,
        timeline,
        states: list[tuple[str, int, bool]],
    ) -> None:
        for media_type, track_idx, was_locked in states:
            if not was_locked:
                continue
            try:
                timeline.SetTrackLock(media_type, track_idx, True)
            except Exception:
                pass

    def _merge_frame_ranges(self, ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
        valid = [(int(start), int(end)) for start, end in ranges if int(end) > int(start)]
        if not valid:
            return []
        valid.sort(key=lambda r: r[0])
        merged: list[tuple[int, int]] = [valid[0]]
        for start, end in valid[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    def _subtract_frame_ranges(
        self,
        base_start: int,
        base_end: int,
        cuts: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        if base_end <= base_start:
            return []

        if not cuts:
            return [(base_start, base_end)]

        remaining: list[tuple[int, int]] = []
        cursor = base_start
        for cut_start, cut_end in cuts:
            if cut_end <= cursor:
                continue
            if cut_start >= base_end:
                break
            if cut_start > cursor:
                remaining.append((cursor, min(cut_start, base_end)))
            cursor = max(cursor, cut_end)
            if cursor >= base_end:
                break

        if cursor < base_end:
            remaining.append((cursor, base_end))

        return remaining

    def rebuild_timeline_keeping(
        self,
        kept_segments: list[dict],
        new_name: str | None = None,
        in_place: bool = False,
    ) -> dict:
        """
        Rebuild the current timeline from the kept segments.
        This is the core "cut the video" operation
        used by silence removal, filler-word removal, and the transcript editor.
        """
        return self.rebuild_timeline_with_audio_mutes(
            kept_segments=kept_segments,
            muted_segments=[],
            new_name=new_name,
            in_place=in_place,
        )

    def rebuild_timeline_with_audio_mutes(
        self,
        kept_segments: list[dict],
        muted_segments: list[dict] | None = None,
        new_name: str | None = None,
        in_place: bool = False,
    ) -> dict:
        """
        Rebuild timeline from kept segments while optionally muting portions of audio.
        Muted regions preserve video and timing, but remove corresponding audio clips.
        """
        timeline = self.get_current_timeline()
        project = self.get_current_project()
        media_pool = self.get_media_pool()

        if timeline is None or project is None or media_pool is None:
            return {"success": False, "error": "No active timeline / project"}

        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0
        tl_start = int(timeline.GetStartFrame())
        tl_end = int(timeline.GetEndFrame())

        merged_kept = self._normalize_time_segments(kept_segments)
        if not merged_kept:
            self._debug("No valid kept segments after normalization")
            return {"success": False, "error": "No valid kept segments"}

        merged_muted_input = self._normalize_time_segments(muted_segments or [], merge_gap=0.02)

        # Clip muted ranges to kept ranges so we never mute outside retained content.
        clipped_muted: list[dict] = []
        keep_idx = 0
        for mute in merged_muted_input:
            mute_start = mute["start"]
            mute_end = mute["end"]

            while keep_idx < len(merged_kept) and merged_kept[keep_idx]["end"] <= mute_start:
                keep_idx += 1

            probe = keep_idx
            while probe < len(merged_kept) and merged_kept[probe]["start"] < mute_end:
                keep = merged_kept[probe]
                overlap_start = max(mute_start, keep["start"])
                overlap_end = min(mute_end, keep["end"])
                if overlap_end > overlap_start:
                    clipped_muted.append({"start": overlap_start, "end": overlap_end})
                probe += 1

        merged_muted = self._normalize_time_segments(clipped_muted, merge_gap=0.02)

        def _segment_to_frame_range(seg: dict) -> tuple[int, int]:
            start_frame = seg.get("start_frame")
            end_frame = seg.get("end_frame")
            if start_frame is None or end_frame is None:
                start_frame = tl_start + int(round(seg["start"] * fps))
                end_frame = tl_start + int(round(seg["end"] * fps))
            start_frame = max(tl_start, min(tl_end, int(start_frame)))
            end_frame = max(tl_start, min(tl_end, int(end_frame)))
            return start_frame, end_frame

        kept_frame_ranges = self._merge_frame_ranges([
            _segment_to_frame_range(seg) for seg in merged_kept
        ])

        if not kept_frame_ranges:
            self._debug("No kept frame ranges after quantization")
            return {"success": False, "error": "No valid kept segments"}

        muted_frame_ranges = self._merge_frame_ranges([
            _segment_to_frame_range(seg) for seg in merged_muted
        ])

        debug_info = {
            "timeline_name": timeline.GetName() if timeline else None,
            "timeline_start_frame": tl_start,
            "timeline_end_frame": tl_end,
            "fps": fps,
            "in_place": in_place,
            "input_segment_count": len(kept_segments),
            "input_muted_count": len(muted_segments or []),
            "normalized_kept_count": len(merged_kept),
            "normalized_muted_count": len(merged_muted),
            "kept_frame_range_count": len(kept_frame_ranges),
            "muted_frame_range_count": len(muted_frame_ranges),
            "kept_seconds_total": round(sum(seg["end"] - seg["start"] for seg in merged_kept), 3),
            "muted_seconds_total": round(sum(seg["end"] - seg["start"] for seg in merged_muted), 3),
            "normalized_kept_sample": merged_kept[:settings.debug_log_limit],
            "normalized_muted_sample": merged_muted[:settings.debug_log_limit],
        }

        self._debug(
            f"rebuild_timeline_with_audio_mutes: timeline={debug_info['timeline_name']}, "
            f"fps={fps}, kept={len(merged_kept)}, muted={len(merged_muted)}"
        )

        # Gather source clips across both video and audio tracks so cuts keep A/V in sync.
        source_clips: list[dict] = []
        source_counts: dict[str, int] = {}
        for media_type in ("video", "audio"):
            track_count = timeline.GetTrackCount(media_type) or 0
            for track_idx in range(1, track_count + 1):
                items = timeline.GetItemListInTrack(media_type, track_idx) or []
                for item in items:
                    try:
                        clip_start = int(item.GetStart())
                        clip_end = int(item.GetEnd())
                        mpi = item.GetMediaPoolItem()
                    except Exception:
                        continue

                    if mpi is None or clip_end <= clip_start:
                        continue

                    source_range = self.get_timeline_item_source_range(item)
                    if source_range is None:
                        continue

                    media_type_code = 1 if media_type == "video" else 2 if media_type == "audio" else None
                    if media_type_code is None:
                        continue

                    source_clips.append({
                        "media_type": media_type,
                        "media_type_code": media_type_code,
                        "track_index": track_idx,
                        "clip_start": clip_start,
                        "clip_end": clip_end,  # exclusive
                        "source_start_frame": int(source_range["source_start_frame"]),
                        "source_end_frame": int(source_range["source_end_frame"]),
                        "media_pool_item": mpi,
                    })
                    key = f"{media_type}:{track_idx}"
                    source_counts[key] = source_counts.get(key, 0) + 1

        if not source_clips:
            self._debug("No usable source clips on timeline")
            response = {"success": False, "error": "No usable source clips on timeline"}
            if self._debug_enabled():
                response["debug"] = debug_info
            return response

        debug_info["source_clip_count"] = len(source_clips)
        debug_info["source_clip_counts_by_track"] = source_counts
        self._debug(f"source clips: total={len(source_clips)}, by_track={source_counts}")

        source_clips.sort(key=lambda c: (c["clip_start"], c["media_type_code"], c["track_index"]))

        if in_place:
            target_timeline = timeline
            tl_name = timeline.GetName()
            try:
                project.SetCurrentTimeline(target_timeline)
            except Exception:
                pass
            self._debug(f"Applying edits in place on timeline: {tl_name}")
        else:
            tl_name = new_name or f"{timeline.GetName()}_edited"
            target_timeline = media_pool.CreateEmptyTimeline(tl_name)
            if target_timeline is None:
                self._debug(f"CreateEmptyTimeline failed for name={tl_name}")
                response = {"success": False, "error": "Resolve refused to create an empty timeline"}
                if self._debug_enabled():
                    response["debug"] = debug_info
                return response
            project.SetCurrentTimeline(target_timeline)
            self._debug(f"Created empty timeline: {tl_name}")

        # Ensure enough tracks exist before appending clip infos.
        for media_type in ("video", "audio"):
            needed = max(
                (clip["track_index"] for clip in source_clips if clip["media_type"] == media_type),
                default=0,
            )
            while (target_timeline.GetTrackCount(media_type) or 0) < needed:
                ok = target_timeline.AddTrack(media_type)
                if not ok:
                    self._debug(f"Failed to add {media_type} track {needed}")
                    response = {"success": False, "error": f"Could not create {media_type} track {needed}"}
                    if self._debug_enabled():
                        response["debug"] = debug_info
                    return response

        clip_infos: list[dict] = []
        output_cursor = 0

        record_frame_base = tl_start if in_place else 0

        for seg_start_frame, seg_end_frame in kept_frame_ranges:
            seg_output_start = output_cursor
            audio_ranges = self._subtract_frame_ranges(seg_start_frame, seg_end_frame, muted_frame_ranges)

            for clip in source_clips:
                if clip["media_type"] == "video":
                    candidate_ranges = [(seg_start_frame, seg_end_frame)]
                else:
                    candidate_ranges = audio_ranges

                for range_start, range_end in candidate_ranges:
                    overlap_start = max(range_start, clip["clip_start"])
                    overlap_end = min(range_end, clip["clip_end"])
                    if overlap_start >= overlap_end:
                        continue

                    src_start = clip["source_start_frame"] + (overlap_start - clip["clip_start"])
                    src_end = clip["source_start_frame"] + (overlap_end - clip["clip_start"]) - 1
                    if src_end < src_start:
                        continue

                    record_frame = record_frame_base + seg_output_start + (overlap_start - seg_start_frame)
                    clip_infos.append({
                        "mediaPoolItem": clip["media_pool_item"],
                        "startFrame": int(src_start),
                        "endFrame": int(src_end),
                        "mediaType": int(clip["media_type_code"]),
                        "trackIndex": int(clip["track_index"]),
                        "recordFrame": int(record_frame),
                    })

            output_cursor += seg_end_frame - seg_start_frame

        if not clip_infos:
            self._debug("No clip infos generated from kept segments")
            response = {"success": False, "error": "No usable segments found"}
            if self._debug_enabled():
                response["debug"] = debug_info
            return response

        debug_info["clip_info_count"] = len(clip_infos)
        debug_info["clip_info_sample"] = [
            {
                "mediaType": ci["mediaType"],
                "trackIndex": ci["trackIndex"],
                "recordFrame": ci["recordFrame"],
                "startFrame": ci["startFrame"],
                "endFrame": ci["endFrame"],
            }
            for ci in clip_infos[:settings.debug_log_limit]
        ]
        self._debug(
            f"clip infos generated: count={len(clip_infos)}, "
            f"sample={debug_info['clip_info_sample']}"
        )

        # Pre-flight validation: verify clip_infos contain video clips
        # before committing to the destructive delete-then-insert path.
        video_clip_count = sum(1 for ci in clip_infos if ci["mediaType"] == 1)
        if in_place and video_clip_count == 0:
            self._debug("Safety abort: no video clips in clip_infos — would leave timeline empty")
            return {"success": False, "error": "No video clips would remain after editing — aborting to protect timeline"}

        # Verify total output frames cover a reasonable portion of input
        if in_place and clip_infos:
            total_output_frames = output_cursor
            total_input_frames = tl_end - tl_start
            if total_input_frames > 0 and total_output_frames / total_input_frames < 0.02:
                self._debug(
                    f"Safety abort: output {total_output_frames} frames is <2% of input {total_input_frames} frames"
                )
                return {
                    "success": False,
                    "error": f"Edits would remove >98% of the timeline ({total_output_frames} of {total_input_frames} frames kept) — aborting to protect timeline",
                }

        track_lock_states = self._unlock_timeline_tracks(target_timeline) if in_place else []
        try:
            if in_place:
                clear_result = self._delete_timeline_clips(target_timeline)
                if not clear_result["success"]:
                    response = {
                        "success": False,
                        "error": f"Failed to clear current timeline before apply: {clear_result.get('error', 'unknown error')}",
                    }
                    if self._debug_enabled():
                        debug_info["clear_error"] = clear_result.get("error")
                        response["debug"] = debug_info
                    return response
                debug_info["cleared_clip_count"] = clear_result["deleted"]
                self._debug(f"cleared timeline clips: {clear_result['deleted']}")

                # Re-obtain media pool — reference may be stale after deletion.
                media_pool = self.get_media_pool()
                if media_pool is None:
                    return {"success": False, "error": "Lost media pool reference after clearing timeline"}

            try:
                project.SetCurrentTimeline(target_timeline)
            except Exception:
                pass

            # Group video+audio clip_infos by (recordFrame, mediaPoolItem) so
            # matching video+audio from the same source are appended together.
            # Resolve auto-links clips from the same media pool item when they
            # appear in the same AppendToTimeline call.
            from collections import defaultdict

            link_groups: dict[tuple, list[dict]] = defaultdict(list)
            for ci in clip_infos:
                mpi_id = id(ci.get("mediaPoolItem"))
                key = (ci["recordFrame"], mpi_id)
                link_groups[key].append(ci)

            # Sort groups by recordFrame so clips are appended in timeline order
            sorted_groups = sorted(link_groups.values(), key=lambda g: g[0]["recordFrame"])

            total_appended = 0

            for group in sorted_groups:
                appended = media_pool.AppendToTimeline(group)
                if appended:
                    appended_count = len(appended) if isinstance(appended, list) else 1
                    total_appended += appended_count
                    self._debug(
                        f"AppendToTimeline linked group succeeded: "
                        f"record={group[0]['recordFrame']}, clips={len(group)}, appended={appended_count}"
                    )
                    continue

                # Grouped call failed — fallback to video then audio separately
                self._debug(
                    f"AppendToTimeline linked group failed at record={group[0]['recordFrame']}, "
                    f"retrying video/audio separately"
                )
                for single in sorted(group, key=lambda c: (c["mediaType"], c["trackIndex"])):
                    single_result = media_pool.AppendToTimeline([single])
                    if single_result:
                        total_appended += 1
                    else:
                        label = "video" if single["mediaType"] == 1 else "audio"
                        self._debug(
                            f"Single clip append failed: {label} track={single['trackIndex']} "
                            f"record={single['recordFrame']} src={single['startFrame']}-{single['endFrame']}"
                        )

            if total_appended == 0:
                self._debug("All AppendToTimeline attempts failed — trying without recordFrame")
                # Last resort: strip recordFrame and let Resolve position clips sequentially
                for group in sorted_groups:
                    for ci in group:
                        stripped = {k: v for k, v in ci.items() if k != "recordFrame"}
                        result_single = media_pool.AppendToTimeline([stripped])
                        if result_single:
                            total_appended += 1

            if total_appended == 0 and in_place:
                self._debug("All in-place append strategies failed — trying fresh timeline")
                # AppendToTimeline may not work on a timeline that had clips deleted.
                # Create a brand-new timeline and try there.
                fallback_name = f"{tl_name}_edited"
                fallback_tl = media_pool.CreateEmptyTimeline(fallback_name)
                if fallback_tl is not None:
                    try:
                        project.SetCurrentTimeline(fallback_tl)
                    except Exception:
                        pass
                    # Recompute clip_infos with record_frame_base=0 for the new timeline
                    fresh_infos = []
                    for ci in clip_infos:
                        fresh_ci = dict(ci)
                        fresh_ci["recordFrame"] = ci["recordFrame"] - tl_start
                        fresh_infos.append(fresh_ci)
                    # Group fresh infos the same way for linked appending
                    fresh_groups: dict[tuple, list[dict]] = defaultdict(list)
                    for ci in fresh_infos:
                        mpi_id = id(ci.get("mediaPoolItem"))
                        key = (ci["recordFrame"], mpi_id)
                        fresh_groups[key].append(ci)
                    for group in sorted(fresh_groups.values(), key=lambda g: g[0]["recordFrame"]):
                        r = media_pool.AppendToTimeline(group)
                        if r:
                            total_appended += len(r) if isinstance(r, list) else 1
                    if total_appended > 0:
                        self._debug(f"Fresh timeline fallback succeeded: {total_appended} clips on {fallback_name}")
                        tl_name = fallback_name
                        target_timeline = fallback_tl
                    else:
                        self._debug("Fresh timeline fallback also failed — deleting it")
                        try:
                            project.SetCurrentTimeline(target_timeline)
                            media_pool.DeleteTimelines([fallback_tl])
                        except Exception:
                            pass

            if total_appended == 0:
                self._debug("All append strategies failed")
                # Attempt to restore original clips so timeline isn't left empty
                self._try_restore_original_clips(media_pool, source_clips, tl_start)
                response = {"success": False, "error": "Resolve failed while appending edited clips — attempted to restore originals"}
                if self._debug_enabled():
                    response["debug"] = debug_info
                return response

            self._debug(f"Total clips appended: {total_appended} of {len(clip_infos)} planned")

            response = {
                "success": True,
                "timeline_name": tl_name,
                "clip_count": total_appended,
                "in_place": in_place,
            }
            if self._debug_enabled():
                response["debug"] = debug_info
            self._debug(f"rebuild complete: timeline={tl_name}, clip_count={len(clip_infos)}")
            return response
        finally:
            if track_lock_states:
                self._restore_timeline_track_locks(target_timeline, track_lock_states)

    def get_all_timelines(self) -> list[dict]:
        """Get info about all timelines in the project."""
        project = self.get_current_project()
        if project is None:
            return []

        timelines = []
        try:
            current_timeline = self.get_current_timeline()
            current_name = None
            if current_timeline is not None:
                try:
                    current_name = current_timeline.GetName()
                except Exception:
                    current_name = None
            count = project.GetTimelineCount()
            for i in range(1, count + 1):
                tl = project.GetTimelineByIndex(i)
                if tl:
                    timeline_name = tl.GetName()
                    is_current = bool(current_timeline is not None and tl == current_timeline)
                    if not is_current and current_name:
                        is_current = timeline_name == current_name
                    timelines.append({
                        "index": i,
                        "name": timeline_name,
                        "is_current": is_current,
                    })
        except Exception:
            pass
        return timelines

    def set_current_timeline_by_index(self, timeline_index: int) -> dict:
        """Switch the active Resolve timeline by 1-based project index."""
        project = self.get_current_project()
        if project is None:
            return {"success": False, "error": "No active Resolve project"}

        try:
            target_index = int(timeline_index)
        except (TypeError, ValueError):
            return {"success": False, "error": "Timeline index must be an integer"}

        if target_index < 1:
            return {"success": False, "error": "Timeline index must be 1 or greater"}

        try:
            target_timeline = project.GetTimelineByIndex(target_index)
        except Exception as exc:
            logger.warning("Failed to look up timeline %s: %s", target_index, exc)
            target_timeline = None
        if target_timeline is None:
            return {"success": False, "error": f"Timeline {target_index} was not found in the current project"}

        try:
            target_name = target_timeline.GetName()
        except Exception:
            target_name = f"Timeline {target_index}"

        try:
            switched = project.SetCurrentTimeline(target_timeline)
        except Exception as exc:
            logger.warning("Failed to switch to timeline %s: %s", target_name, exc)
            return {"success": False, "error": f"Could not switch to {target_name}: {exc}"}

        active_timeline = self.get_current_timeline()
        active_name = None
        if active_timeline is not None:
            try:
                active_name = active_timeline.GetName()
            except Exception:
                active_name = None

        success = switched is not False
        if active_name is not None:
            success = active_name == target_name
        if not success:
            return {"success": False, "error": f"Resolve did not switch to {target_name}"}

        try:
            info = get_project_info(self._resolve)
            if info is not None:
                self._last_project_info = info
        except Exception:
            pass

        return {
            "success": True,
            "timeline_index": target_index,
            "timeline_name": target_name,
        }


# Singleton instance
resolve_service = ResolveService()
