"""Export service: generate EDL and FCPXML files from timeline data."""

import logging
import os
from pathlib import Path
from config import settings

logger = logging.getLogger("finlaycut.export")


class ExportService:
    def __init__(self):
        self.export_dir = settings.temp_dir / "exports"
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _frames_to_timecode(self, frame: int, fps: float) -> str:
        """Convert frame number to SMPTE timecode HH:MM:SS:FF."""
        total_frames = int(frame)
        f = total_frames % int(fps)
        total_seconds = total_frames // int(fps)
        s = total_seconds % 60
        total_minutes = total_seconds // 60
        m = total_minutes % 60
        h = total_minutes // 60
        return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

    def _seconds_to_timecode(self, seconds: float, fps: float) -> str:
        """Convert seconds to SMPTE timecode."""
        total_frames = int(round(seconds * fps))
        return self._frames_to_timecode(total_frames, fps)

    def generate_edl(
        self,
        clips: list[dict],
        timeline_name: str = "Timeline",
        fps: float = 24.0,
    ) -> str:
        """
        Generate an EDL (Edit Decision List) file from clip data.

        Each clip dict should have:
          - name: str (clip name/reel)
          - record_start: float (timeline start in seconds)
          - record_end: float (timeline end in seconds)
          - source_start: float (source start in seconds)
          - source_end: float (source end in seconds)
          - track_type: str ("V" or "A")
        """
        lines = [f"TITLE: {timeline_name}", "FCM: NON-DROP FRAME", ""]

        for i, clip in enumerate(clips, 1):
            event_num = f"{i:03d}"
            reel = clip.get("name", "AX")[:8].ljust(8)
            track_type = clip.get("track_type", "V")
            transition = "C"  # Cut

            src_in = self._seconds_to_timecode(clip.get("source_start", 0), fps)
            src_out = self._seconds_to_timecode(clip.get("source_end", 0), fps)
            rec_in = self._seconds_to_timecode(clip.get("record_start", 0), fps)
            rec_out = self._seconds_to_timecode(clip.get("record_end", 0), fps)

            line = f"{event_num}  {reel} {track_type}     {transition}        {src_in} {src_out} {rec_in} {rec_out}"
            lines.append(line)

            # Add clip name comment
            if clip.get("name"):
                lines.append(f"* FROM CLIP NAME: {clip['name']}")
            lines.append("")

        edl_content = "\n".join(lines)

        # Write to file
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in timeline_name)
        output_path = str(self.export_dir / f"{safe_name}.edl")
        with open(output_path, "w") as f:
            f.write(edl_content)

        logger.info("Generated EDL: %s (%d events)", output_path, len(clips))
        return output_path

    def generate_fcpxml(
        self,
        clips: list[dict],
        timeline_name: str = "Timeline",
        fps: float = 24.0,
        duration_seconds: float = 0,
    ) -> str:
        """
        Generate a minimal FCPXML v1.9 file.

        Each clip dict should have the same fields as generate_edl().
        """
        fps_num = int(fps * 1000)
        fps_den = 1000

        clip_elements = []
        for clip in clips:
            name = clip.get("name", "Clip")
            src_start = clip.get("source_start", 0)
            src_end = clip.get("source_end", 0)
            rec_start = clip.get("record_start", 0)
            duration = src_end - src_start

            # FCPXML uses rational time format
            offset_frames = int(round(rec_start * fps))
            duration_frames = int(round(duration * fps))
            start_frames = int(round(src_start * fps))

            clip_elements.append(
                f'            <clip name="{_xml_escape(name)}" '
                f'offset="{offset_frames}/{int(fps)}s" '
                f'duration="{duration_frames}/{int(fps)}s" '
                f'start="{start_frames}/{int(fps)}s">'
                f"\n            </clip>"
            )

        total_frames = int(round(duration_seconds * fps))
        clips_xml = "\n".join(clip_elements)

        fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.9">
    <resources>
        <format id="r1" frameDuration="{fps_den}/{fps_num}s" width="1920" height="1080"/>
    </resources>
    <library>
        <event name="{_xml_escape(timeline_name)}">
            <project name="{_xml_escape(timeline_name)}">
                <sequence format="r1" duration="{total_frames}/{int(fps)}s">
                    <spine>
{clips_xml}
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>
"""
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in timeline_name)
        output_path = str(self.export_dir / f"{safe_name}.fcpxml")
        with open(output_path, "w") as f:
            f.write(fcpxml)

        logger.info("Generated FCPXML: %s (%d clips)", output_path, len(clips))
        return output_path


def _xml_escape(text: str) -> str:
    """Escape special XML characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


export_service = ExportService()
