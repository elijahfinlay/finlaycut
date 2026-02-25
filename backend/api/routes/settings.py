"""Settings management endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from config import settings
from services.ai_service import ai_service

router = APIRouter()


class SettingsUpdate(BaseModel):
    ai_backend: Optional[str] = None
    claude_api_key: Optional[str] = None
    ollama_model: Optional[str] = None
    whisper_model: Optional[str] = None
    silence_threshold_db: Optional[int] = None
    silence_min_duration_ms: Optional[int] = None
    silence_padding_ms: Optional[int] = None
    filler_words: Optional[list[str]] = None
    pexels_api_key: Optional[str] = None
    cache_limit_gb: Optional[float] = None
    backup_retention_days: Optional[int] = None


@router.get("/")
async def get_settings():
    """Get current settings."""
    ollama_available = await ai_service.check_ollama_available()

    return {
        "ai_backend": settings.ai_backend,
        "ollama_available": ollama_available,
        "ollama_model": settings.ollama_model,
        "has_claude_key": bool(settings.claude_api_key),
        "whisper_model": settings.whisper_model,
        "silence_threshold_db": settings.silence_threshold_db,
        "silence_min_duration_ms": settings.silence_min_duration_ms,
        "silence_padding_ms": settings.silence_padding_ms,
        "filler_words": settings.filler_words,
        "has_pexels_key": bool(settings.pexels_api_key),
        "cache_limit_gb": settings.cache_limit_gb,
        "backup_retention_days": settings.backup_retention_days,
    }


@router.put("/")
async def update_settings(update: SettingsUpdate):
    """Update settings."""
    if update.ai_backend is not None:
        settings.ai_backend = update.ai_backend
        ai_service.set_backend(update.ai_backend)

    if update.claude_api_key is not None:
        settings.claude_api_key = update.claude_api_key if update.claude_api_key else None

    if update.ollama_model is not None:
        settings.ollama_model = update.ollama_model

    if update.whisper_model is not None:
        settings.whisper_model = update.whisper_model

    if update.silence_threshold_db is not None:
        settings.silence_threshold_db = update.silence_threshold_db

    if update.silence_min_duration_ms is not None:
        settings.silence_min_duration_ms = update.silence_min_duration_ms

    if update.silence_padding_ms is not None:
        settings.silence_padding_ms = update.silence_padding_ms

    if update.filler_words is not None:
        settings.filler_words = update.filler_words

    if update.pexels_api_key is not None:
        settings.pexels_api_key = update.pexels_api_key if update.pexels_api_key else None

    if update.cache_limit_gb is not None:
        settings.cache_limit_gb = update.cache_limit_gb

    if update.backup_retention_days is not None:
        settings.backup_retention_days = update.backup_retention_days

    return {"success": True, "message": "Settings updated"}


@router.post("/clear-cache")
async def clear_cache():
    """Clear the transcription cache."""
    import shutil
    cache_dir = settings.cache_dir
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if f.is_file():
                f.unlink()
    return {"success": True, "message": "Cache cleared"}


@router.get("/cache-size")
async def get_cache_size():
    """Get current cache size."""
    total = 0
    cache_dir = settings.cache_dir
    if cache_dir.exists():
        for f in cache_dir.rglob("*"):
            if f.is_file():
                total += f.stat().st_size

    return {
        "size_bytes": total,
        "size_mb": round(total / (1024 * 1024), 1),
        "limit_gb": settings.cache_limit_gb
    }
