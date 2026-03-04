"""Audio utility functions using ffmpeg."""

import logging
import os
import subprocess
import shutil
import hashlib
from pathlib import Path

import numpy as np

from config import settings

logger = logging.getLogger("finlaycut.audio_utils")

# ── Ensure homebrew paths are on PATH (Electron may strip them) ──
_EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin"]
_cur = os.environ.get("PATH", "")
for _p in _EXTRA_PATHS:
    if _p not in _cur:
        _cur = _p + ":" + _cur
os.environ["PATH"] = _cur

# Cached full path to ffmpeg (resolved once)
_FFMPEG_PATH: str | None = None


def _media_cache_suffix(path: str) -> str:
    """Build a stable short suffix from the source path and file metadata."""
    try:
        stat = os.stat(path)
        raw = f"{os.path.abspath(path)}:{stat.st_size}:{stat.st_mtime_ns}"
    except Exception:
        raw = os.path.abspath(path)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def get_ffmpeg_path() -> str:
    """Find ffmpeg — resolved once and cached."""
    global _FFMPEG_PATH
    if _FFMPEG_PATH is not None:
        return _FFMPEG_PATH

    path = shutil.which("ffmpeg")
    if path:
        _FFMPEG_PATH = path
        return path
    # Common macOS locations
    for candidate in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.exists(candidate):
            _FFMPEG_PATH = candidate
            return candidate
    raise FileNotFoundError("ffmpeg not found. Install with: brew install ffmpeg")


def load_audio_np(file: str, sr: int = 16000) -> np.ndarray:
    """Load audio as a float32 numpy array using the resolved ffmpeg path.

    Drop-in replacement for whisperx.load_audio that avoids the bare 'ffmpeg'
    subprocess call which fails inside Electron (PATH gets stripped).
    """
    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg,
        "-nostdin",
        "-threads", "0",
        "-i", file,
        "-f", "s16le",
        "-ac", "1",
        "-acodec", "pcm_s16le",
        "-ar", str(sr),
        "-",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to load audio: {e.stderr.decode()}") from e

    return np.frombuffer(out, np.int16).flatten().astype(np.float32) / 32768.0


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
        suffix = _media_cache_suffix(video_path)
        output_path = str(settings.temp_dir / f"{name}_{suffix}_audio.wav")

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
    duration = end_seconds - start_seconds

    # Guard: if the segment covers the whole file or is trivially small,
    # just return the full audio to avoid empty-file issues.
    if start_seconds <= 0.01 and duration <= 0:
        return audio_path

    # Clamp start to 0 to avoid negative seeks
    start_seconds = max(0.0, start_seconds)
    duration = end_seconds - start_seconds
    if duration <= 0.05:
        # Segment too short — just use the full audio
        logger.debug("Segment too short (%.3fs), using full audio", duration)
        return audio_path

    if output_path is None:
        name = Path(audio_path).stem
        start_ms = int(round(start_seconds * 1000))
        end_ms = int(round(end_seconds * 1000))
        output_path = str(
            settings.temp_dir / f"{name}_{start_ms}_{end_ms}.wav"
        )

    # Check that start_seconds is within the source audio duration
    try:
        source_duration = get_audio_duration(audio_path)
        if start_seconds >= source_duration:
            logger.debug("start (%.1fs) >= source duration (%.1fs), using full audio", start_seconds, source_duration)
            return audio_path
        # Clamp end to source duration
        if end_seconds > source_duration:
            end_seconds = source_duration
            duration = end_seconds - start_seconds
    except Exception:
        pass  # If we can't get duration, try anyway

    ffmpeg = get_ffmpeg_path()
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

    # Verify the output file has actual content
    file_size = os.path.getsize(output_path)
    if file_size < 1000:  # WAV header is ~44 bytes, anything under 1KB is empty
        logger.warning("Extracted segment is empty (%s bytes), using full audio", file_size)
        return audio_path

    return output_path


def assemble_timeline_audio(
    clip_segments: list[dict],
    timeline_duration_seconds: float,
    sample_rate: int = 16000,
) -> str:
    """
    Assemble audio from multiple timeline clips into a single WAV file.

    Each item in clip_segments should have:
        - audio_path: path to the extracted audio for this clip's source range
        - timeline_start_seconds: when this clip starts on the timeline
        - duration_seconds: how long this clip is on the timeline

    Returns path to the assembled WAV file.
    """
    from pydub import AudioSegment
    # Force pydub to use the full ffmpeg path (Electron strips PATH)
    AudioSegment.converter = get_ffmpeg_path()
    AudioSegment.ffmpeg = AudioSegment.converter

    total_ms = int(timeline_duration_seconds * 1000)
    timeline_audio = AudioSegment.silent(duration=total_ms, frame_rate=sample_rate)

    for seg in clip_segments:
        try:
            clip_audio = AudioSegment.from_file(seg["audio_path"])
            if clip_audio.frame_rate != sample_rate:
                clip_audio = clip_audio.set_frame_rate(sample_rate)
            if clip_audio.channels != 1:
                clip_audio = clip_audio.set_channels(1)

            # Truncate clip audio to its timeline duration to prevent overlay extension
            clip_duration_ms = int(seg["duration_seconds"] * 1000)
            if len(clip_audio) > clip_duration_ms:
                clip_audio = clip_audio[:clip_duration_ms]

            position_ms = int(seg["timeline_start_seconds"] * 1000)
            timeline_audio = timeline_audio.overlay(clip_audio, position=position_ms)
        except Exception as e:
            logger.warning("Failed to process clip audio at %.1fs: %s",
                           seg["timeline_start_seconds"], e)

    output_path = str(settings.temp_dir / "timeline_full_audio.wav")
    # Export as WAV without extra parameters — audio is already 16kHz mono
    # from extract_audio().  Omitting parameters lets pydub use its fast
    # native WAV writer instead of spawning ffmpeg.
    timeline_audio.export(output_path, format="wav")

    logger.info("Assembled timeline audio: %.1fs, %d clips -> %s",
                timeline_duration_seconds, len(clip_segments), output_path)
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
