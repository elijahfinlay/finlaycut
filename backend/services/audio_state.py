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
_last_audio_source: str | None = None


def set_last_transcription_audio(
    path: str,
    duration: float,
    offset: float = 0.0,
    timeline_signature: str | None = None,
    covers_full_timeline: bool = False,
    audio_source: str | None = None,
) -> None:
    global _last_audio_path, _last_audio_duration, _last_audio_offset
    global _last_audio_timeline_signature, _last_audio_covers_full_timeline, _last_audio_source
    _last_audio_path = path
    _last_audio_duration = duration
    _last_audio_offset = offset
    _last_audio_timeline_signature = timeline_signature
    _last_audio_covers_full_timeline = covers_full_timeline
    _last_audio_source = audio_source


def get_last_transcription_audio() -> tuple[str, float, float, str | None, bool, str | None] | None:
    if _last_audio_path is not None and _last_audio_duration is not None:
        return (
            _last_audio_path,
            _last_audio_duration,
            _last_audio_offset,
            _last_audio_timeline_signature,
            _last_audio_covers_full_timeline,
            _last_audio_source,
        )
    return None


def unpack_last_transcription_audio(
    cached: tuple | None,
) -> tuple[str, float, float, str | None, bool, str | None] | None:
    if cached is None:
        return None

    if len(cached) == 6:
        path, duration, offset, timeline_signature, covers_full_timeline, audio_source = cached
        return (
            str(path),
            float(duration),
            float(offset),
            str(timeline_signature) if timeline_signature is not None else None,
            bool(covers_full_timeline),
            str(audio_source) if audio_source is not None else None,
        )

    if len(cached) == 5:
        path, duration, offset, timeline_signature, covers_full_timeline = cached
        return (
            str(path),
            float(duration),
            float(offset),
            str(timeline_signature) if timeline_signature is not None else None,
            bool(covers_full_timeline),
            None,
        )

    raise ValueError("Unexpected cached transcription audio tuple shape")
