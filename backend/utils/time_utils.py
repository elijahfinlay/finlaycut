"""Time conversion utilities for frame/timecode/seconds."""


def seconds_to_frames(seconds: float, fps: float) -> int:
    """Convert seconds to frame number."""
    return int(round(seconds * fps))


def frames_to_seconds(frames: int, fps: float) -> float:
    """Convert frame number to seconds."""
    return frames / fps


def seconds_to_timecode(seconds: float, fps: float = 24.0) -> str:
    """Convert seconds to HH:MM:SS:FF timecode."""
    total_frames = int(round(seconds * fps))
    hours = total_frames // (3600 * int(fps))
    remaining = total_frames % (3600 * int(fps))
    minutes = remaining // (60 * int(fps))
    remaining = remaining % (60 * int(fps))
    secs = remaining // int(fps)
    frames = remaining % int(fps)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


def timecode_to_seconds(timecode: str, fps: float = 24.0) -> float:
    """Convert HH:MM:SS:FF timecode to seconds."""
    parts = timecode.split(":")
    if len(parts) == 4:
        h, m, s, f = [int(p) for p in parts]
        return h * 3600 + m * 60 + s + f / fps
    elif len(parts) == 3:
        h, m, s = [int(p) for p in parts]
        return h * 3600 + m * 60 + s
    raise ValueError(f"Invalid timecode format: {timecode}")


def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def srt_time_to_seconds(srt_time: str) -> float:
    """Convert SRT timestamp to seconds."""
    time_part, millis_part = srt_time.split(",")
    parts = time_part.split(":")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    return h * 3600 + m * 60 + s + int(millis_part) / 1000


def format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m"
