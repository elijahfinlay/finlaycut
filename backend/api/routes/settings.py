"""Settings management endpoints."""

import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from config import settings
from services.ai_service import ai_service
from services.clip_search_service import clip_search_service

logger = logging.getLogger("finlaycut.settings")
router = APIRouter()


class SettingsUpdate(BaseModel):
    ai_backend: Optional[str] = None
    claude_api_key: Optional[str] = None
    claude_model: Optional[str] = None
    vision_provider: Optional[str] = None
    gemini_api_key: Optional[str] = None
    vision_max_parallelism: Optional[int] = Field(None, ge=1, le=16)
    clip_search_storage_dir: Optional[str] = None
    ollama_model: Optional[str] = None
    whisper_model: Optional[str] = None
    silence_threshold_db: Optional[int] = Field(None, ge=-80, le=0)
    silence_min_duration_ms: Optional[int] = Field(None, ge=50, le=10000)
    silence_min_duration_frames: Optional[int] = Field(None, ge=14, le=240)
    silence_padding_ms: Optional[int] = Field(None, ge=0, le=5000)
    filler_words: Optional[list[str]] = None
    pexels_api_key: Optional[str] = None
    cache_limit_gb: Optional[float] = Field(None, gt=0, le=100)
    backup_retention_days: Optional[int] = Field(None, ge=1, le=365)


@router.get("/")
async def get_settings():
    """Get current settings."""
    ollama_available = await ai_service.check_ollama_available()

    return {
        "ai_backend": settings.ai_backend,
        "ollama_available": ollama_available,
        "ollama_model": settings.ollama_model,
        "claude_model": settings.claude_model,
        "has_claude_key": bool(settings.claude_api_key),
        "vision_provider": settings.vision_provider,
        "has_gemini_key": bool(settings.gemini_api_key),
        "vision_max_parallelism": settings.vision_max_parallelism,
        "clip_search_storage_dir": settings.clip_search_storage_dir,
        "clip_search_storage_default_dir": str(settings.app_data_dir),
        **{
            f"clip_search_storage_{key}": value
            for key, value in clip_search_service.get_storage_info().items()
        },
        "whisper_model": settings.whisper_model,
        "silence_threshold_db": settings.silence_threshold_db,
        "silence_min_duration_ms": settings.silence_min_duration_ms,
        "silence_min_duration_frames": max(14, int(settings.silence_min_duration_frames)),
        "silence_padding_ms": settings.silence_padding_ms,
        "filler_words": settings.filler_words,
        "has_pexels_key": bool(settings.pexels_api_key),
        "cache_limit_gb": settings.cache_limit_gb,
        "backup_retention_days": settings.backup_retention_days,
    }


@router.put("/")
async def update_settings(update: SettingsUpdate):
    """Update settings."""
    previous_clip_search_db_path = clip_search_service.get_storage_info()["effective_db_path"]

    if update.ai_backend is not None:
        settings.ai_backend = update.ai_backend
        ai_service.set_backend(update.ai_backend)

    if update.claude_api_key is not None:
        settings.claude_api_key = update.claude_api_key if update.claude_api_key else None

    if update.claude_model is not None:
        settings.claude_model = update.claude_model

    if update.vision_provider is not None:
        settings.vision_provider = update.vision_provider

    if update.gemini_api_key is not None:
        settings.gemini_api_key = update.gemini_api_key if update.gemini_api_key else None

    if update.vision_max_parallelism is not None:
        settings.vision_max_parallelism = int(update.vision_max_parallelism)

    clip_search_storage_update = None
    if update.clip_search_storage_dir is not None:
        normalized = str(update.clip_search_storage_dir or "").strip()
        if normalized:
            try:
                storage_dir = Path(normalized).expanduser().resolve()
                storage_dir.mkdir(parents=True, exist_ok=True)
                probe_path = storage_dir / ".finlaycut_clip_search_probe"
                probe_path.write_text("ok", encoding="utf-8")
                probe_path.unlink(missing_ok=True)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Clip Search storage folder is not writable: {exc}",
                ) from exc
            settings.clip_search_storage_dir = str(storage_dir)
        else:
            settings.clip_search_storage_dir = None
        clip_search_storage_update = clip_search_service.refresh_storage_location(previous_clip_search_db_path)

    if update.ollama_model is not None:
        settings.ollama_model = update.ollama_model

    if update.whisper_model is not None:
        settings.whisper_model = update.whisper_model

    if update.silence_threshold_db is not None:
        settings.silence_threshold_db = update.silence_threshold_db

    if update.silence_min_duration_ms is not None:
        settings.silence_min_duration_ms = update.silence_min_duration_ms

    if update.silence_min_duration_frames is not None:
        settings.silence_min_duration_frames = max(14, int(update.silence_min_duration_frames))

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

    settings.save_to_disk()
    logger.info("Settings updated and saved to disk")

    response = {"success": True, "message": "Settings updated"}
    if clip_search_storage_update is not None:
        response.update(
            {
                "clip_search_storage_dir": settings.clip_search_storage_dir,
                "clip_search_storage_db_path": clip_search_storage_update["configured_db_path"],
                "clip_search_storage_effective_db_path": clip_search_storage_update["effective_db_path"],
                "clip_search_storage_migrated": clip_search_storage_update["migrated"],
            }
        )
    return response


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
