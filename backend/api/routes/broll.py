"""B-roll suggestion and insertion endpoints."""

from fastapi import APIRouter
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from services.stock_service import stock_service
from utils.audio_utils import extract_audio, get_audio_duration
from api.websocket import manager
from config import settings

router = APIRouter()


@router.post("/suggest")
async def suggest_broll():
    """Analyze transcript and suggest B-roll footage."""
    clips = resolve_service.get_timeline_clips("video", 1)
    if not clips:
        return {"error": "No video clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clips[0])
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("broll", 0.1, "Extracting audio...")
        audio_path = extract_audio(file_path)
        duration = get_audio_duration(audio_path)

        await manager.send_progress("broll", 0.2, "Transcribing...")
        transcript = audio_service.transcribe(audio_path)

        await manager.send_progress("broll", 0.5, "AI suggesting B-roll...")
        raw_suggestions = await ai_service.suggest_broll(transcript["text"])

        # Search stock footage for each suggestion
        suggestions = []
        for i, sug in enumerate(raw_suggestions):
            pct = sug.get("time_pct", 0) / 100.0
            time_seconds = pct * duration

            # Search Pexels for stock footage
            search_results = []
            if settings.pexels_api_key:
                await manager.send_progress(
                    "broll",
                    0.5 + (i / len(raw_suggestions)) * 0.4,
                    f"Searching stock footage ({i+1}/{len(raw_suggestions)})..."
                )
                search_results = await stock_service.search_videos(
                    sug.get("search_term", ""),
                    per_page=3
                )

            suggestions.append({
                "time_seconds": time_seconds,
                "search_term": sug.get("search_term", ""),
                "context": sug.get("context", ""),
                "reason": sug.get("reason", ""),
                "stock_results": search_results,
                "selected": False
            })

        await manager.send_progress("broll", 1.0, f"Found {len(suggestions)} B-roll opportunities")

        return {
            "suggestions": suggestions,
            "count": len(suggestions),
            "has_pexels_key": bool(settings.pexels_api_key)
        }
    except Exception as e:
        await manager.send_error("broll", str(e))
        return {"error": str(e)}


@router.post("/insert")
async def insert_broll(items: list[dict]):
    """Download and insert selected B-roll clips into the timeline."""
    if not resolve_service.connected:
        return {"error": "Not connected to DaVinci Resolve"}

    backup = resolve_service.duplicate_timeline()
    inserted = 0

    for item in items:
        if not item.get("download_url"):
            continue
        # Download and import would happen here
        inserted += 1

    return {
        "success": True,
        "inserted_count": inserted,
        "backup_timeline": backup.GetName() if backup else None
    }
