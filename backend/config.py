import json
import logging
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional

logger = logging.getLogger("finlaycut.config")

SETTINGS_FILE = Path.home() / ".finlaycut" / "settings.json"
LEGACY_FILLER_WORDS = [
    "um", "uh", "uh-huh", "uhh", "uhm", "umm",
    "hmm", "hm", "mmhmm", "mhm", "mm", "mmm",
    "huh", "er", "erm", "ah", "ahh",
]
DEFAULT_FILLER_WORDS = ["um", "uh", "uhm", "umm", "uhh"]

# Fields that are persisted to disk
PERSISTED_FIELDS = [
    "ai_backend", "ollama_base_url", "ollama_model", "claude_api_key",
    "claude_model", "whisper_model", "silence_threshold_db",
    "silence_min_duration_ms", "silence_min_duration_frames", "silence_padding_ms", "filler_words",
    "pexels_api_key", "cache_limit_gb", "backup_retention_days",
    "vision_provider", "gemini_api_key", "vision_max_parallelism", "clip_search_storage_dir",
    "alignment_method", "alignment_waveform_refine", "alignment_confidence_threshold",
    "alignment_timing_offset_ms",
]


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

    # WhisperX — "medium" gives the best balance of accuracy and speed.
    # Uses faster-whisper (CTranslate2) + forced alignment for precise word timestamps.
    whisper_model: str = "medium"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"  # "float16" for GPU, "int8" for CPU

    # Alignment
    alignment_method: str = "mms_fa"              # "mms_fa" | "whisperx"
    alignment_waveform_refine: bool = True        # post-alignment waveform refinement
    alignment_confidence_threshold: float = 0.5   # below = flagged low confidence
    alignment_timing_offset_ms: float = 50.0      # positive = shift words earlier (ms)

    # Silence Detection Defaults
    silence_threshold_db: int = -40
    silence_min_duration_ms: int = 300
    silence_min_duration_frames: int = 14
    silence_padding_ms: int = 100

    # Filler Words
    filler_words: list[str] = DEFAULT_FILLER_WORDS.copy()

    # AI Backend
    ai_backend: str = "ollama"  # "ollama" or "claude"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    claude_api_key: Optional[str] = None
    claude_model: str = "claude-sonnet-4-5-20250514"

    # Vision
    vision_provider: str = "gemini"
    gemini_api_key: Optional[str] = None
    vision_max_parallelism: int = 4
    clip_search_storage_dir: Optional[str] = None

    # B-Roll
    pexels_api_key: Optional[str] = None

    # Cache
    cache_limit_gb: float = 2.0
    backup_retention_days: int = 30

    # Debugging
    debug_cut_planning: bool = False
    debug_log_limit: int = 12

    # Accuracy feature flags
    enable_spoken_waveform_refiner: bool = False
    enable_master_mix_analysis_default: bool = True

    # Spoken waveform refinement tuning
    spoken_refine_left_search_ms: int = 220
    spoken_refine_right_search_ms: int = 320
    spoken_refine_start_guard_ms: int = 12
    spoken_refine_end_guard_ms: int = 32
    spoken_refine_min_duration_ms: int = 30
    spoken_refine_zero_cross_window_ms: float = 2.0
    spoken_refine_expand_confidence_threshold: float = 0.52
    spoken_refine_fallback_confidence_threshold: float = 0.28
    spoken_refine_low_confidence_max_expand_ms: int = 45

    class Config:
        env_prefix = "FINLAYCUT_"
        env_file = ".env"

    def save_to_disk(self):
        """Persist user-configurable settings to ~/.finlaycut/settings.json."""
        data = {}
        for field in PERSISTED_FIELDS:
            val = getattr(self, field, None)
            data[field] = val
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps(data, indent=2))
            logger.info("Settings saved to %s", SETTINGS_FILE)
        except Exception as e:
            logger.error("Failed to save settings: %s", e)

    def load_from_disk(self):
        """Load persisted settings from disk on startup."""
        if not SETTINGS_FILE.exists():
            return
        migrated = False
        try:
            data = json.loads(SETTINGS_FILE.read_text())
            if data.get("filler_words") == LEGACY_FILLER_WORDS:
                data["filler_words"] = DEFAULT_FILLER_WORDS.copy()
                migrated = True
            for field in PERSISTED_FIELDS:
                if field in data and data[field] is not None:
                    setattr(self, field, data[field])
            if migrated:
                self.save_to_disk()
            logger.info("Settings loaded from %s", SETTINGS_FILE)
        except Exception as e:
            logger.error("Failed to load settings: %s", e)


settings = Settings()

# Ensure directories exist
for d in [settings.app_data_dir, settings.cache_dir, settings.backups_dir, settings.temp_dir]:
    d.mkdir(parents=True, exist_ok=True)

# Load persisted settings on import
settings.load_from_disk()
