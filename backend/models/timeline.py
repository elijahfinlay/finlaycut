from pydantic import BaseModel
from typing import Optional


class TimelineInfo(BaseModel):
    name: str
    start_frame: int
    end_frame: int
    duration_frames: int
    framerate: str
    resolution_width: Optional[str] = None
    resolution_height: Optional[str] = None
    video_tracks: int
    audio_tracks: int
    subtitle_tracks: int


class ClipInfo(BaseModel):
    name: str
    start: int  # Frame position on timeline
    end: int
    duration: int
    source_start: int
    source_end: int
    file_path: Optional[str] = None
    track_type: str = "video"
    track_index: int = 1


class UndoEntry(BaseModel):
    id: str
    operation: str
    timestamp: float
    timeline_name: str
    backup_timeline_name: str
    description: str
