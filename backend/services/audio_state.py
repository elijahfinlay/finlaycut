"""Shared state for the last transcription audio path and duration.

After transcription extracts/assembles audio, it stores the result here
so the waveform endpoint can reuse the same audio file instead of
re-extracting from Resolve (which can produce a different duration).
"""

_last_audio_path: str | None = None
_last_audio_duration: float | None = None


def set_last_transcription_audio(path: str, duration: float) -> None:
    global _last_audio_path, _last_audio_duration
    _last_audio_path = path
    _last_audio_duration = duration


def get_last_transcription_audio() -> tuple[str, float] | None:
    if _last_audio_path is not None and _last_audio_duration is not None:
        return (_last_audio_path, _last_audio_duration)
    return None
