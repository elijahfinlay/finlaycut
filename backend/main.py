import os
import sys
import shutil

# ── MUST run before any other imports ──────────────────────────────
# Electron strips /opt/homebrew/bin from PATH. Fix it immediately so
# every subprocess (ffmpeg, ffprobe) and every library (pydub) works.
_EXTRA_PATHS = ["/opt/homebrew/bin", "/usr/local/bin"]
_current_path = os.environ.get("PATH", "")
for _p in _EXTRA_PATHS:
    if _p not in _current_path:
        _current_path = _p + ":" + _current_path
os.environ["PATH"] = _current_path

# ── Resolve absolute ffmpeg/ffprobe paths ONCE and patch pydub ─────
# pydub.utils.get_encoder_name() returns bare "ffmpeg" (not the path)
# which breaks inside Electron.  Monkey-patch it early so every
# library that touches pydub gets the right binary.
def _resolve_bin(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    for d in ["/opt/homebrew/bin", "/usr/local/bin"]:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate):
            return candidate
    return name  # fallback to bare name

_FFMPEG_ABS = _resolve_bin("ffmpeg")
_FFPROBE_ABS = _resolve_bin("ffprobe")

try:
    import pydub.utils as _pu
    _pu.get_encoder_name = lambda: _FFMPEG_ABS
    _pu.get_prober_name = lambda: _FFPROBE_ABS
    # Also fix the class attribute that was already set at import time
    from pydub import AudioSegment as _AS
    _AS.converter = _FFMPEG_ABS
    _AS.ffprobe = _FFPROBE_ABS
except Exception:
    pass

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from api.router import api_router
from api.websocket import ws_router
from services.resolve_service import resolve_service
from config import settings

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
# Quiet noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

logger = logging.getLogger("finlaycut")


def _cleanup_stale_temp_files():
    """Remove temp files older than 1 hour on startup."""
    import time
    temp_dir = settings.temp_dir
    if not temp_dir.exists():
        return
    cutoff = time.time() - 3600
    count = 0
    for f in temp_dir.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
    if count:
        logger.info("Cleaned up %d stale temp files", count)


def _preload_whisper():
    """Preload the WhisperX model in a background thread so first transcription is fast."""
    try:
        from services.audio_service import audio_service
        audio_service.load_whisper_model(settings.whisper_model)
    except Exception as e:
        logger.warning("WhisperX preload skipped: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FinlayCut backend starting up")

    # Clean stale temp files from previous sessions
    _cleanup_stale_temp_files()

    # Startup: try initial connection to Resolve
    resolve_service.try_connect()

    # Start background polling for Resolve state changes
    poll_task = asyncio.create_task(resolve_service.poll_loop())

    # Preload Whisper model in a background thread (non-blocking)
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _preload_whisper)

    yield

    # Shutdown
    logger.info("FinlayCut backend shutting down")
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="FinlayCut Backend",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5175",
        "http://localhost:5175",
        "http://127.0.0.1:5176",
        "http://localhost:5176",
        "http://127.0.0.1:5177",
        "http://localhost:5177",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.include_router(ws_router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all for unhandled exceptions. Logs the full traceback and returns
    a clean 500 so the backend stays alive instead of crashing."""
    logger.error(
        "Unhandled exception on %s %s: %s\n%s",
        request.method, request.url.path, exc,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"error": f"Internal server error: {type(exc).__name__}: {exc}"},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.get("/debug/ffmpeg")
async def debug_ffmpeg():
    """Temporary debug endpoint — remove after fixing ffmpeg issue."""
    import shutil
    import os
    info = {
        "PATH": os.environ.get("PATH", ""),
        "shutil_which_ffmpeg": shutil.which("ffmpeg"),
        "homebrew_exists": os.path.exists("/opt/homebrew/bin/ffmpeg"),
    }
    try:
        from utils.audio_utils import get_ffmpeg_path
        info["get_ffmpeg_path"] = get_ffmpeg_path()
    except Exception as e:
        info["get_ffmpeg_path_error"] = str(e)
    try:
        from pydub import AudioSegment
        info["pydub_converter"] = AudioSegment.converter
    except Exception as e:
        info["pydub_error"] = str(e)
    try:
        import subprocess
        r = subprocess.run([info.get("get_ffmpeg_path", "ffmpeg"), "-version"],
                          capture_output=True, text=True, timeout=5)
        info["ffmpeg_runs"] = r.returncode == 0
        info["ffmpeg_version"] = r.stdout.split("\n")[0] if r.stdout else ""
    except Exception as e:
        info["ffmpeg_run_error"] = str(e)
    return info
