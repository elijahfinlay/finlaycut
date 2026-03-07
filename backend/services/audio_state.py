"""Shared state for the last transcription audio path and duration.

After transcription extracts/assembles audio, it stores the result here
so the waveform endpoint can reuse the same audio file instead of
re-extracting from Resolve (which can produce a different duration).
"""

_last_audio_path: str | None = None
_last_audio_duration: float | None = None
_last_audio_offset: float = 0.0
_last_audio_timeline_signature: str | None = None
_last_audio_covers_full_timeline: bool = False


def set_last_transcription_audio(
    path: str,
    duration: float,
    offset: float = 0.0,
    timeline_signature: str | None = None,
    covers_full_timeline: bool = False,
) -> None:
    global _last_audio_path, _last_audio_duration, _last_audio_offset
    global _last_audio_timeline_signature, _last_audio_covers_full_timeline
    _last_audio_path = path
    _last_audio_duration = duration
    _last_audio_offset = offset
    _last_audio_timeline_signature = timeline_signature
    _last_audio_covers_full_timeline = covers_full_timeline


def get_last_transcription_audio() -> tuple[str, float, float, str | None, bool] | None:
    if _last_audio_path is not None and _last_audio_duration is not None:
        return (
            _last_audio_path,
            _last_audio_duration,
            _last_audio_offset,
            _last_audio_timeline_signature,
            _last_audio_covers_full_timeline,
        )
    return None
