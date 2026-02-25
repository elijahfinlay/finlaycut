import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Server
    host: str = "127.0.0.1"
    port: int = 42069

    # Paths
    app_data_dir: Path = Path.home() / ".finlaycut"
    cache_dir: Path = Path.home() / ".finlaycut" / "cache"
    backups_dir: Path = Path.home() / ".finlaycut" / "backups"
    temp_dir: Path = Path.home() / ".finlaycut" / "temp"

    # Resolve
    resolve_script_api: str = os.getenv(
        "RESOLVE_SCRIPT_API",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    )
    resolve_script_lib: str = os.getenv(
        "RESOLVE_SCRIPT_LIB",
        "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    )

    # Whisper
    whisper_model: str = "base"
    whisper_device: str = "cpu"

    # Silence Detection Defaults
    silence_threshold_db: int = -40
    silence_min_duration_ms: int = 300
    silence_padding_ms: int = 100

    # Filler Words
    filler_words: list[str] = [
        "um", "uh", "like", "you know", "basically", "actually",
        "literally", "right", "so", "well", "I mean", "kind of",
        "sort of", "er", "ah"
    ]

    # AI Backend
    ai_backend: str = "ollama"  # "ollama" or "claude"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    claude_api_key: Optional[str] = None

    # B-Roll
    pexels_api_key: Optional[str] = None

    # Cache
    cache_limit_gb: float = 2.0
    backup_retention_days: int = 30

    class Config:
        env_prefix = "FINLAYCUT_"
        env_file = ".env"


settings = Settings()

# Ensure directories exist
for d in [settings.app_data_dir, settings.cache_dir, settings.backups_dir, settings.temp_dir]:
    d.mkdir(parents=True, exist_ok=True)
