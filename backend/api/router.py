from fastapi import APIRouter
from api.routes import (
    resolve_status,
    silence,
    filler_words,
    captions,
    zoom,
    chapters,
    podcast,
    repetition,
    viral,
    broll,
    audio_enhance,
    thumbnail,
    settings as settings_route,
    undo,
    transcript_editor,
    waveform,
    export,
    scenes,
    diarization,
)

api_router = APIRouter()

api_router.include_router(resolve_status.router, prefix="/resolve", tags=["resolve"])
api_router.include_router(silence.router, prefix="/silence", tags=["silence"])
api_router.include_router(filler_words.router, prefix="/filler-words", tags=["filler-words"])
api_router.include_router(captions.router, prefix="/captions", tags=["captions"])
api_router.include_router(zoom.router, prefix="/zoom", tags=["zoom"])
api_router.include_router(chapters.router, prefix="/chapters", tags=["chapters"])
api_router.include_router(podcast.router, prefix="/podcast", tags=["podcast"])
api_router.include_router(repetition.router, prefix="/repetition", tags=["repetition"])
api_router.include_router(viral.router, prefix="/viral", tags=["viral"])
api_router.include_router(broll.router, prefix="/broll", tags=["broll"])
api_router.include_router(audio_enhance.router, prefix="/audio", tags=["audio"])
api_router.include_router(thumbnail.router, prefix="/thumbnail", tags=["thumbnail"])
api_router.include_router(settings_route.router, prefix="/settings", tags=["settings"])
api_router.include_router(undo.router, prefix="/undo", tags=["undo"])
api_router.include_router(transcript_editor.router, prefix="/transcript-editor", tags=["transcript-editor"])
api_router.include_router(waveform.router, prefix="/waveform", tags=["waveform"])
api_router.include_router(export.router, prefix="/export", tags=["export"])
api_router.include_router(scenes.router, prefix="/scenes", tags=["scenes"])
api_router.include_router(diarization.router, prefix="/diarization", tags=["diarization"])
