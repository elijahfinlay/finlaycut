"""Video analysis service: zoom calculations, reframing."""

from typing import Optional
import numpy as np


class VideoService:
    def calculate_zoom_points(
        self,
        transcript: dict,
        energy_data: list[dict],
        min_gap_seconds: float = 3.0,
        zoom_amount: float = 1.15,
        zoom_duration_seconds: float = 2.0,
        max_zooms: int = 50
    ) -> list[dict]:
        """
        Calculate optimal zoom points by combining transcript emphasis
        with audio energy peaks.

        Returns list of zoom points with:
            - time: center time in seconds
            - duration: zoom duration
            - zoom: zoom multiplier
            - reason: why this point was chosen
            - confidence: 0-1 score
        """
        # Score each moment based on energy + transcript features
        candidates = []

        # 1. Sentence starts (from transcript segments)
        for seg in transcript.get("segments", []):
            candidates.append({
                "time": seg["start"],
                "score": 0.5,
                "reason": "sentence_start"
            })

        # 2. High energy peaks
        if energy_data:
            energies = [e["energy"] for e in energy_data]
            threshold = np.percentile(energies, 85) if energies else 0.7

            for point in energy_data:
                if point["energy"] >= threshold:
                    candidates.append({
                        "time": point["time"],
                        "score": point["energy"],
                        "reason": "energy_peak"
                    })

        # 3. Merge nearby candidates and keep the highest scoring
        candidates.sort(key=lambda c: c["time"])
        merged = []
        for c in candidates:
            if merged and abs(c["time"] - merged[-1]["time"]) < min_gap_seconds:
                if c["score"] > merged[-1]["score"]:
                    merged[-1] = c
            else:
                merged.append(c)

        # 4. Sort by score, take top N
        merged.sort(key=lambda c: c["score"], reverse=True)
        top_points = merged[:max_zooms]
        top_points.sort(key=lambda c: c["time"])

        # 5. Build zoom point entries
        zoom_points = []
        for c in top_points:
            zoom_points.append({
                "time": c["time"],
                "duration": zoom_duration_seconds,
                "zoom": zoom_amount,
                "pan_x": 0.0,  # Center by default
                "pan_y": 0.0,
                "reason": c["reason"],
                "confidence": min(c["score"], 1.0),
                "selected": True
            })

        return zoom_points

    def calculate_vertical_crop(
        self,
        source_width: int,
        source_height: int,
        target_aspect: tuple[int, int] = (9, 16)
    ) -> dict:
        """
        Calculate crop parameters for vertical (9:16) reframing.
        Used for Shorts/Reels/TikToks export.
        """
        target_ratio = target_aspect[0] / target_aspect[1]
        source_ratio = source_width / source_height

        if source_ratio > target_ratio:
            # Source is wider — crop sides
            new_width = int(source_height * target_ratio)
            crop_x = (source_width - new_width) // 2
            return {
                "crop_left": crop_x,
                "crop_right": source_width - new_width - crop_x,
                "crop_top": 0,
                "crop_bottom": 0,
                "output_width": new_width,
                "output_height": source_height
            }
        else:
            # Source is taller — crop top/bottom
            new_height = int(source_width / target_ratio)
            crop_y = (source_height - new_height) // 2
            return {
                "crop_left": 0,
                "crop_right": 0,
                "crop_top": crop_y,
                "crop_bottom": source_height - new_height - crop_y,
                "output_width": source_width,
                "output_height": new_height
            }


video_service = VideoService()
