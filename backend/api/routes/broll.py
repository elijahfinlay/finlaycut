"""B-roll suggestion and insertion endpoints."""

import logging
import time
from fastapi import APIRouter
from services.resolve_service import resolve_service
from services.audio_service import audio_service
from services.ai_service import ai_service
from services.stock_service import stock_service
from utils.audio_utils import extract_audio, extract_audio_segment, get_audio_duration
from utils.operation_queue import heavy_operation
from api.websocket import manager
from config import settings

logger = logging.getLogger("finlaycut.broll")

router = APIRouter()


@router.post("/suggest")
@heavy_operation("B-Roll Suggestions")
async def suggest_broll():
    """Analyze transcript and suggest B-roll footage."""
    clip = resolve_service.get_clip_at_playhead("video", 1)
    if clip is None:
        return {"error": "No video clips found in timeline"}

    file_path = resolve_service.get_clip_file_path(clip)
    if not file_path:
        return {"error": "Could not determine source file path"}

    try:
        await manager.send_progress("broll", 0.02, "Extracting audio...")

        timeline = resolve_service.get_current_timeline()
        fps_str = timeline.GetSetting("timelineFrameRate") if timeline else None
        fps = float(fps_str) if fps_str else 24.0
        timeline_start_frame = timeline.GetStartFrame() if timeline else 0
        clip_timeline_offset = (clip.GetStart() - timeline_start_frame) / fps
        left_offset = clip.GetLeftOffset()
        clip_duration_frames = clip.GetEnd() - clip.GetStart()
        source_start = left_offset / fps
        source_end = (left_offset + clip_duration_frames) / fps

        full_audio = extract_audio(file_path)
        audio_path = extract_audio_segment(full_audio, source_start, source_end)
        duration = get_audio_duration(audio_path)

        async def progress_fn(pct, msg):
            scaled = 0.05 + pct * 0.40
            await manager.send_progress("broll", scaled, msg)

        transcript = await audio_service.transcribe_with_progress(
            audio_path, progress_fn=progress_fn,
        )

        await manager.send_progress("broll", 0.5, "AI suggesting B-roll...")
        raw_suggestions = await ai_service.suggest_broll(transcript["text"])

        # Search stock footage for each suggestion
        suggestions = []
        for i, sug in enumerate(raw_suggestions):
            pct = sug.get("time_pct", 0) / 100.0
            time_seconds = clip_timeline_offset + (pct * duration)

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

    if not items:
        return {"error": "No B-roll items to insert"}

    # Create backup (non-blocking — proceed even if backup fails)
    backup = resolve_service.duplicate_timeline()
    backup_name = "none"
    if backup is not None:
        try:
            backup_name = backup.GetName()
        except Exception:
            backup_name = "backup"
    else:
        logger.warning("Could not create backup timeline")

    timeline = resolve_service.get_current_timeline()
    media_pool = resolve_service.get_media_pool()
    if timeline is None or media_pool is None:
        return {"error": "No active timeline"}

    fps_str = timeline.GetSetting("timelineFrameRate")
    fps = float(fps_str) if fps_str else 24.0
    tl_start = timeline.GetStartFrame()

    if timeline.GetTrackCount("video") < 2:
        timeline.AddTrack("video")

    await manager.send_progress("broll_insert", 0.1, "Downloading B-roll clips...")

    inserted = 0
    for i, item in enumerate(items):
        stock_results = item.get("stock_results", [])
        if not stock_results:
            continue

        # Pick the first stock result that has a download URL
        download_url = None
        for sr in stock_results:
            url = sr.get("download_url") or sr.get("url")
            if url:
                download_url = url
                break

        if not download_url:
            continue

        pct = 0.1 + (i / len(items)) * 0.7
        await manager.send_progress("broll_insert", pct, f"Downloading clip {i+1}...")

        try:
            # Download the video file
            import httpx
            import os

            temp_dir = settings.temp_dir
            temp_path = os.path.join(str(temp_dir), f"broll_{i}_{int(time.time())}.mp4")

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(download_url)
                if resp.status_code == 200:
                    with open(temp_path, "wb") as f:
                        f.write(resp.content)

                    # Import into Resolve's media pool
                    imported = media_pool.ImportMedia([temp_path])
                    if imported:
                        imported_item = imported[0]
                        target_seconds = float(item.get("time_seconds", 0))
                        target_record_frame = tl_start + int(round(target_seconds * fps))

                        # Resolve expects inclusive endFrame and often exposes
                        # clip duration in the "Frames" property.
                        props = imported_item.GetClipProperty() if imported_item else {}
                        frames_raw = props.get("Frames") if props else None
                        clip_frames = None
                        if frames_raw not in (None, ""):
                            try:
                                clip_frames = int(float(frames_raw))
                            except Exception:
                                clip_frames = None
                        if (clip_frames is None or clip_frames <= 1) and props:
                            duration_tc = props.get("Duration")
                            if duration_tc and ":" in str(duration_tc):
                                try:
                                    hh, mm, ss, ff = str(duration_tc).replace(";", ":").split(":")
                                    clip_frames = (
                                        (int(hh) * 3600 + int(mm) * 60 + int(ss)) * int(round(fps))
                                    ) + int(ff)
                                except Exception:
                                    clip_frames = None

                        clip_info = {
                            "mediaPoolItem": imported_item,
                            "trackIndex": 2,
                            "mediaType": "video",
                            "recordFrame": target_record_frame,
                        }

                        if clip_frames and clip_frames > 1:
                            clip_info["startFrame"] = 0
                            clip_info["endFrame"] = clip_frames - 1

                        ok = media_pool.AppendToTimeline([clip_info])
                        if not ok:
                            # Fallback: still append if structured insert fails.
                            media_pool.AppendToTimeline(imported)
                        inserted += 1
        except Exception as e:
            logger.error("Failed to download/import clip %s: %s", i, e)
            continue

    await manager.send_progress("broll_insert", 1.0, "Done")

    from api.routes.undo import _record
    _record("B-Roll Insert", backup_name,
            f"Inserted {inserted} B-roll clips")

    return {
        "success": True,
        "inserted_count": inserted,
        "backup_timeline": backup_name,
        "message": f"Inserted {inserted} B-roll clips. Backup: {backup_name}"
    }
