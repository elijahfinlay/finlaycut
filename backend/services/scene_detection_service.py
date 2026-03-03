"""Scene detection service using visual change analysis."""

import logging
import threading
import asyncio
import time
from typing import Callable, Awaitable, Optional
from utils.audio_utils import get_ffmpeg_path
from config import settings

logger = logging.getLogger("finlaycut.scene_detection")


class SceneDetectionService:
    def detect_scenes(self, video_path: str, threshold: float = 27.0) -> list[dict]:
        """
        Detect scene changes in a video file.
        Uses scenedetect library (ContentDetector).

        Returns list of dicts with:
          - time_seconds: float
          - frame_number: int
          - score: float (0-100 confidence)
        """
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector

        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=threshold))
        scene_manager.detect_scenes(video)

        scene_list = scene_manager.get_scene_list()
        scenes = []
        for i, (start, end) in enumerate(scene_list):
            scenes.append({
                "index": i,
                "start_seconds": start.get_seconds(),
                "end_seconds": end.get_seconds(),
                "start_frame": start.get_frames(),
                "end_frame": end.get_frames(),
                "duration": end.get_seconds() - start.get_seconds(),
            })

        return scenes

    async def detect_scenes_with_progress(
        self,
        video_path: str,
        threshold: float = 27.0,
        progress_fn: Optional[Callable[[float, str], Awaitable]] = None,
    ) -> list[dict]:
        """Detect scenes in a background thread with progress updates."""
        result_holder = [None]
        error_holder = [None]
        done_event = threading.Event()

        def run_detect():
            try:
                result_holder[0] = self.detect_scenes(video_path, threshold)
            except Exception as e:
                error_holder[0] = e
            finally:
                done_event.set()

        if progress_fn:
            await progress_fn(0.1, "Starting scene detection...")

        thread = threading.Thread(target=run_detect, daemon=True)
        start_time = time.time()
        thread.start()

        while not done_event.is_set():
            elapsed = time.time() - start_time
            # Estimate progress (scene detection speed varies)
            pct = min(0.1 + elapsed / 60.0 * 0.8, 0.9)
            if progress_fn:
                await progress_fn(pct, f"Analyzing scenes... ({int(elapsed)}s)")
            await asyncio.sleep(1.0)

        thread.join()

        if error_holder[0] is not None:
            raise error_holder[0]

        scenes = result_holder[0]

        if progress_fn:
            await progress_fn(1.0, f"Found {len(scenes)} scenes")

        return scenes

    def detect_scenes_ffmpeg(self, video_path: str, threshold: float = 0.3) -> list[dict]:
        """
        Fallback scene detection using ffmpeg's scene filter.
        Doesn't require scenedetect library.

        Args:
            threshold: Scene change threshold (0.0-1.0). Lower = more sensitive.
        """
        import subprocess
        import json

        ffmpeg = get_ffmpeg_path()
        cmd = [
            ffmpeg,
            "-i", video_path,
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null",
            "-"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        stderr = result.stderr

        scenes = []
        for line in stderr.split("\n"):
            if "pts_time:" in line:
                try:
                    pts_part = line.split("pts_time:")[1]
                    time_str = pts_part.split()[0]
                    time_seconds = float(time_str)
                    scenes.append({
                        "index": len(scenes),
                        "start_seconds": time_seconds,
                        "end_seconds": time_seconds,
                        "start_frame": 0,
                        "end_frame": 0,
                        "duration": 0,
                    })
                except (IndexError, ValueError):
                    continue

        logger.info("FFmpeg scene detection found %d scenes", len(scenes))
        return scenes


scene_detection_service = SceneDetectionService()
