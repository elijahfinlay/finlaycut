"""Audio utility functions using ffmpeg."""

import os
import subprocess
import shutil
from pathlib import Path
from config import settings


def get_ffmpeg_path() -> str:
    """Find ffmpeg in PATH or common locations."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    # Common macOS locations
    for candidate in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError("ffmpeg not found. Install with: brew install ffmpeg")


def extract_audio(
    video_path: str,
    output_path: str | None = None,
    sample_rate: int = 16000,
    mono: bool = True
) -> str:
    """
    Extract audio from a video file as WAV.

    Args:
        video_path: Path to the video/audio source
        output_path: Where to save the WAV. If None, uses temp dir.
        sample_rate: Audio sample rate (16000 for Whisper)
        mono: Whether to convert to mono

    Returns:
        Path to the extracted WAV file
    """
    if output_path is None:
        name = Path(video_path).stem
        output_path = str(settings.temp_dir / f"{name}_audio.wav")

    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vn",  # No video
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
    ]
    if mono:
        cmd.extend(["-ac", "1"])
    cmd.append(output_path)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")

    return output_path


def extract_audio_segment(
    audio_path: str,
    start_seconds: float,
    end_seconds: float,
    output_path: str | None = None
) -> str:
    """Extract a segment of audio between start and end times."""
    if output_path is None:
        name = Path(audio_path).stem
        output_path = str(
            settings.temp_dir / f"{name}_{start_seconds:.1f}_{end_seconds:.1f}.wav"
        )

    ffmpeg = get_ffmpeg_path()
    duration = end_seconds - start_seconds
    cmd = [
        ffmpeg, "-y",
        "-i", audio_path,
        "-ss", str(start_seconds),
        "-t", str(duration),
        "-acodec", "pcm_s16le",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg segment extraction failed: {result.stderr}")

    return output_path


def get_audio_duration(audio_path: str) -> float:
    """Get the duration of an audio file in seconds."""
    ffmpeg_dir = os.path.dirname(get_ffmpeg_path())
    ffprobe = os.path.join(ffmpeg_dir, "ffprobe")
    if not os.path.exists(ffprobe):
        ffprobe = shutil.which("ffprobe") or "ffprobe"

    cmd = [
        ffprobe,
        "-i", audio_path,
        "-show_entries", "format=duration",
        "-v", "quiet",
        "-of", "csv=p=0"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    return float(result.stdout.strip())


def extract_frame(
    video_path: str,
    time_seconds: float,
    output_path: str | None = None,
    width: int = 1920
) -> str:
    """Extract a single frame from a video file as JPEG."""
    if output_path is None:
        name = Path(video_path).stem
        output_path = str(settings.temp_dir / f"{name}_frame_{time_seconds:.1f}.jpg")

    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg, "-y",
        "-ss", str(time_seconds),
        "-i", video_path,
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", "2",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extraction failed: {result.stderr}")

    return output_path
