"""Clip search analysis, storage, and query service."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from services.resolve_service import resolve_service
from utils.audio_utils import extract_frame
from utils.time_utils import seconds_to_srt_time

logger = logging.getLogger("finlaycut.clip_search")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in value)
    return safe[:96] or "clip"


def _build_analysis_key(timeline_signature: str, scope_in_frame: int, scope_out_frame: int, track_index: int) -> str:
    raw = f"{timeline_signature}:{scope_in_frame}:{scope_out_frame}:{track_index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _build_clip_key_from_context(context: dict) -> str:
    clip_item_id = str(context.get("clip_item_id") or "").strip()
    if clip_item_id:
        return clip_item_id
    raw = ":".join(
        [
            str(context.get("file_path") or ""),
            str(context.get("clip_start_frame") or ""),
            str(context.get("clip_end_frame") or ""),
            str(context.get("source_start_frame") or ""),
            str(context.get("source_end_frame") or ""),
        ]
    )
    return f"clip-{hashlib.sha1(raw.encode('utf-8')).hexdigest()}"


def _build_clip_key_from_word(word: dict) -> str | None:
    clip_item_id = str(word.get("clip_item_id") or "").strip()
    if clip_item_id:
        return clip_item_id
    file_path = str(word.get("file_path") or "").strip()
    if file_path:
        source_start = word.get("source_start_frame")
        source_end = word.get("source_end_frame")
        if source_start is not None or source_end is not None:
            start_text = "" if source_start is None else str(source_start)
            end_text = "" if source_end is None else str(source_end)
            return f"path:{file_path}:{start_text}:{end_text}"
        return f"path:{file_path}"
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(text)
    return deduped


def _normalize_labels(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return _dedupe([piece.strip() for piece in raw.split(",")])
    if isinstance(raw, list):
        return _dedupe([str(item).strip() for item in raw])
    return []


def _build_summary_text(summary: str, shot_type: str, labels: list[str], ocr_text: list[str]) -> str:
    summary = str(summary or "").strip()
    if summary:
        return summary
    parts: list[str] = []
    if shot_type:
        parts.append(shot_type)
    if labels:
        parts.append(", ".join(labels[:3]))
    if ocr_text:
        parts.append(f"text: {', '.join(ocr_text[:2])}")
    return " - ".join(parts) if parts else "Visual clip"


def _format_overlay_text(row: dict) -> str:
    shot_type = str(row.get("shot_type") or "").strip()
    labels = _normalize_labels(row.get("labels_json") and json.loads(row["labels_json"]))
    ocr_values = _normalize_labels(str(row.get("ocr_text") or "").split(" | "))
    text_parts: list[str] = []
    if shot_type:
        text_parts.append(shot_type)
    if labels:
        text_parts.append(", ".join(labels[:3]))
    text = " - ".join(text_parts) if text_parts else str(row.get("summary_text") or "Visual clip").strip()
    if ocr_values:
        candidate = f"{text} - text: {', '.join(ocr_values[:2])}"
        if len(candidate) <= 80:
            text = candidate
    return text or "Visual clip"


def _format_snippets(row: sqlite3.Row) -> list[dict]:
    snippets: list[dict] = []
    for field, label in (
        ("labels_snippet", "labels"),
        ("summary_snippet", "summary"),
        ("ocr_snippet", "ocr"),
        ("transcript_snippet", "transcript"),
    ):
        value = row[field] if field in row.keys() else None
        text = str(value or "").strip()
        if text:
            snippets.append({"field": label, "text": text})
    return snippets


def _configured_storage_dir_path() -> Path:
    configured = str(settings.clip_search_storage_dir or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(settings.app_data_dir)


def _configured_db_path() -> Path:
    return _configured_storage_dir_path() / "clip_search.sqlite3"


class ClipSearchStore:
    def __init__(self, db_path: str | Path | None = None):
        self.requested_db_path = Path(db_path) if db_path is not None else _configured_db_path()
        self.db_path = self.requested_db_path
        self._ensure_schema()

    @staticmethod
    def _fallback_db_path() -> Path:
        return Path("/tmp/finlaycut") / "clip_search.sqlite3"

    def _select_db_path(self) -> Path:
        primary_path = Path(self.requested_db_path)
        fallback_path = self._fallback_db_path()
        if self.db_path == fallback_path and primary_path != fallback_path:
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            return fallback_path
        if primary_path == fallback_path:
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            return fallback_path

        try:
            primary_path.parent.mkdir(parents=True, exist_ok=True)
            if os.access(primary_path.parent, os.W_OK):
                return primary_path
            logger.warning("Clip search DB directory is not writable at %s; falling back to %s", primary_path.parent, fallback_path)
        except OSError as exc:
            logger.warning("Clip search DB directory unavailable at %s (%s); falling back to %s", primary_path.parent, exc, fallback_path)

        fallback_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = fallback_path
        return fallback_path

    @staticmethod
    def _configure_connection(conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA foreign_keys=ON")

    def _connect(self) -> sqlite3.Connection:
        chosen_path = self._select_db_path()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(chosen_path)
            self._configure_connection(conn)
            self.db_path = chosen_path
            return conn
        except Exception:
            if conn is not None:
                conn.close()
            raise

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS analysis_run (
                    analysis_key TEXT PRIMARY KEY,
                    project_name TEXT,
                    timeline_name TEXT,
                    timeline_signature TEXT NOT NULL,
                    scope_in_frame INTEGER NOT NULL,
                    scope_out_frame INTEGER NOT NULL,
                    track_index INTEGER NOT NULL,
                    scope_mode TEXT NOT NULL,
                    provider_name TEXT,
                    provider_model TEXT,
                    status TEXT NOT NULL,
                    clip_count INTEGER NOT NULL DEFAULT 0,
                    transcript_available INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS clip_analysis (
                    analysis_key TEXT NOT NULL,
                    clip_key TEXT NOT NULL,
                    clip_item_id TEXT,
                    clip_name TEXT NOT NULL,
                    track_index INTEGER NOT NULL,
                    file_path TEXT,
                    timeline_start_frame INTEGER NOT NULL,
                    timeline_end_frame INTEGER NOT NULL,
                    timeline_start_seconds REAL NOT NULL,
                    timeline_end_seconds REAL NOT NULL,
                    source_start_frame INTEGER,
                    source_end_frame INTEGER,
                    summary_text TEXT NOT NULL,
                    shot_type TEXT,
                    labels_json TEXT NOT NULL,
                    labels_text TEXT NOT NULL,
                    ocr_text TEXT,
                    transcript_text TEXT,
                    provider_name TEXT,
                    provider_model TEXT,
                    provider_payload_json TEXT,
                    error_state TEXT,
                    error_message TEXT,
                    analyzed_at TEXT NOT NULL,
                    PRIMARY KEY (analysis_key, clip_key),
                    FOREIGN KEY (analysis_key) REFERENCES analysis_run(analysis_key) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS clip_transcript (
                    timeline_signature TEXT NOT NULL,
                    clip_key TEXT NOT NULL,
                    clip_item_id TEXT,
                    file_path TEXT,
                    text TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (timeline_signature, clip_key)
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS clip_search_fts USING fts5(
                    analysis_key UNINDEXED,
                    clip_key UNINDEXED,
                    clip_name,
                    labels_text,
                    summary_text,
                    ocr_text,
                    transcript_text
                );

                CREATE INDEX IF NOT EXISTS idx_analysis_run_signature
                    ON analysis_run (timeline_signature, scope_in_frame, scope_out_frame, track_index);

                CREATE INDEX IF NOT EXISTS idx_clip_analysis_timing
                    ON clip_analysis (analysis_key, timeline_start_frame);

                CREATE INDEX IF NOT EXISTS idx_clip_transcript_signature
                    ON clip_transcript (timeline_signature);
                """
            )

    def start_analysis(
        self,
        analysis_key: str,
        *,
        project_name: str | None,
        timeline_name: str | None,
        timeline_signature: str,
        scope_in_frame: int,
        scope_out_frame: int,
        track_index: int,
        scope_mode: str,
        provider_name: str,
        provider_model: str,
    ) -> None:
        now = _utcnow_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO analysis_run (
                    analysis_key, project_name, timeline_name, timeline_signature,
                    scope_in_frame, scope_out_frame, track_index, scope_mode,
                    provider_name, provider_model, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
                ON CONFLICT(analysis_key) DO UPDATE SET
                    project_name=excluded.project_name,
                    timeline_name=excluded.timeline_name,
                    timeline_signature=excluded.timeline_signature,
                    scope_in_frame=excluded.scope_in_frame,
                    scope_out_frame=excluded.scope_out_frame,
                    track_index=excluded.track_index,
                    scope_mode=excluded.scope_mode,
                    provider_name=excluded.provider_name,
                    provider_model=excluded.provider_model,
                    status='running',
                    clip_count=0,
                    transcript_available=0,
                    last_error=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    analysis_key,
                    project_name,
                    timeline_name,
                    timeline_signature,
                    int(scope_in_frame),
                    int(scope_out_frame),
                    int(track_index),
                    scope_mode,
                    provider_name,
                    provider_model,
                    now,
                    now,
                ),
            )

    def complete_analysis(
        self,
        analysis_key: str,
        *,
        rows: list[dict],
        transcript_available: bool,
        provider_name: str,
        provider_model: str,
    ) -> None:
        now = _utcnow_iso()
        with self._connection() as conn:
            conn.execute("DELETE FROM clip_analysis WHERE analysis_key = ?", (analysis_key,))
            conn.execute("DELETE FROM clip_search_fts WHERE analysis_key = ?", (analysis_key,))
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO clip_analysis (
                        analysis_key, clip_key, clip_item_id, clip_name, track_index, file_path,
                        timeline_start_frame, timeline_end_frame, timeline_start_seconds, timeline_end_seconds,
                        source_start_frame, source_end_frame, summary_text, shot_type, labels_json, labels_text,
                        ocr_text, transcript_text, provider_name, provider_model, provider_payload_json,
                        error_state, error_message, analyzed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_key,
                        row["clip_key"],
                        row.get("clip_item_id"),
                        row["clip_name"],
                        int(row["track_index"]),
                        row.get("file_path"),
                        int(row["timeline_start_frame"]),
                        int(row["timeline_end_frame"]),
                        float(row["timeline_start_seconds"]),
                        float(row["timeline_end_seconds"]),
                        int(row["source_start_frame"]) if row.get("source_start_frame") is not None else None,
                        int(row["source_end_frame"]) if row.get("source_end_frame") is not None else None,
                        row["summary_text"],
                        row.get("shot_type"),
                        json.dumps(row.get("labels", [])),
                        row["labels_text"],
                        row.get("ocr_text"),
                        row.get("transcript_text"),
                        provider_name,
                        provider_model,
                        json.dumps(row.get("provider_payload") or {}),
                        row.get("error_state"),
                        row.get("error_message"),
                        row.get("analyzed_at") or now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO clip_search_fts (
                        analysis_key, clip_key, clip_name, labels_text, summary_text, ocr_text, transcript_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_key,
                        row["clip_key"],
                        row["clip_name"],
                        row["labels_text"],
                        row["summary_text"],
                        row.get("ocr_text") or "",
                        row.get("transcript_text") or "",
                    ),
                )
            conn.execute(
                """
                UPDATE analysis_run
                SET status='complete',
                    clip_count=?,
                    transcript_available=?,
                    provider_name=?,
                    provider_model=?,
                    last_error=NULL,
                    updated_at=?
                WHERE analysis_key=?
                """,
                (
                    len(rows),
                    1 if transcript_available else 0,
                    provider_name,
                    provider_model,
                    now,
                    analysis_key,
                ),
            )

    def fail_analysis(self, analysis_key: str, error_message: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE analysis_run
                SET status='error', last_error=?, updated_at=?
                WHERE analysis_key=?
                """,
                (error_message, _utcnow_iso(), analysis_key),
            )

    def persist_transcript_words(
        self,
        timeline_signature: str,
        words: list[dict],
        *,
        replace_existing: bool = False,
    ) -> int:
        grouped: dict[str, list[str]] = {}
        clip_item_ids: dict[str, str | None] = {}
        file_paths: dict[str, str | None] = {}
        for word in words:
            clip_key = _build_clip_key_from_word(word)
            if not clip_key:
                continue
            text = str(word.get("word", "")).strip()
            kind = str(word.get("kind") or "").strip()
            if not text or text.startswith("[") or kind in {"silence", "audio_hesitation"}:
                continue
            grouped.setdefault(clip_key, []).append(text)
            clip_item_ids[clip_key] = word.get("clip_item_id")
            file_paths[clip_key] = word.get("file_path")

        with self._connection() as conn:
            if replace_existing:
                conn.execute("DELETE FROM clip_transcript WHERE timeline_signature = ?", (timeline_signature,))
            for clip_key, tokens in grouped.items():
                conn.execute(
                    """
                    INSERT INTO clip_transcript (
                        timeline_signature, clip_key, clip_item_id, file_path, text, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(timeline_signature, clip_key) DO UPDATE SET
                        clip_item_id=excluded.clip_item_id,
                        file_path=excluded.file_path,
                        text=excluded.text,
                        updated_at=excluded.updated_at
                    """,
                    (
                        timeline_signature,
                        clip_key,
                        clip_item_ids.get(clip_key),
                        file_paths.get(clip_key),
                        " ".join(tokens),
                        _utcnow_iso(),
                    ),
                )
        return len(grouped)

    def load_transcript_map(self, timeline_signature: str) -> dict[str, str]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT clip_key, text FROM clip_transcript WHERE timeline_signature = ?",
                (timeline_signature,),
            ).fetchall()
        return {str(row["clip_key"]): str(row["text"] or "") for row in rows}

    def get_status(self, analysis_key: str, current_timeline_signature: str | None = None) -> dict | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM analysis_run
                WHERE analysis_key = ?
                """,
                (analysis_key,),
            ).fetchone()
        if row is None:
            return None
        status = dict(row)
        status["stale"] = bool(
            current_timeline_signature
            and str(status.get("timeline_signature") or "") != str(current_timeline_signature)
        )
        return status

    def query(self, analysis_key: str, query_text: str, limit: int = 50) -> list[dict]:
        with self._connection() as conn:
            if not query_text.strip():
                rows = conn.execute(
                    """
                    SELECT *
                    FROM clip_analysis
                    WHERE analysis_key = ?
                    ORDER BY timeline_start_frame ASC
                    LIMIT ?
                    """,
                    (analysis_key, int(limit)),
                ).fetchall()
            else:
                match_query = self._to_match_query(query_text)
                rows = conn.execute(
                    """
                    SELECT
                        ca.*,
                        bm25(clip_search_fts, 0.0, 0.0, 0.5, 5.0, 4.0, 3.0, 1.5) AS rank,
                        snippet(clip_search_fts, 3, '[', ']', ' ... ', 8) AS labels_snippet,
                        snippet(clip_search_fts, 4, '[', ']', ' ... ', 8) AS summary_snippet,
                        snippet(clip_search_fts, 5, '[', ']', ' ... ', 8) AS ocr_snippet,
                        snippet(clip_search_fts, 6, '[', ']', ' ... ', 8) AS transcript_snippet
                    FROM clip_search_fts
                    JOIN clip_analysis AS ca
                      ON ca.analysis_key = clip_search_fts.analysis_key
                     AND ca.clip_key = clip_search_fts.clip_key
                    WHERE clip_search_fts.analysis_key = ?
                      AND clip_search_fts MATCH ?
                    ORDER BY rank ASC, ca.timeline_start_frame ASC
                    LIMIT ?
                    """,
                    (analysis_key, match_query, int(limit)),
                ).fetchall()
        return [self._row_to_result(row) for row in rows]

    def get_analysis_rows(self, analysis_key: str) -> list[dict]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM clip_analysis
                WHERE analysis_key = ?
                ORDER BY timeline_start_frame ASC
                """,
                (analysis_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_analysis(self, analysis_key: str) -> int:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS clip_count
                FROM clip_analysis
                WHERE analysis_key = ?
                """,
                (analysis_key,),
            ).fetchone()
            clip_count = int(row["clip_count"]) if row is not None else 0
            conn.execute("DELETE FROM clip_search_fts WHERE analysis_key = ?", (analysis_key,))
            conn.execute("DELETE FROM clip_analysis WHERE analysis_key = ?", (analysis_key,))
            conn.execute("DELETE FROM analysis_run WHERE analysis_key = ?", (analysis_key,))
        return clip_count

    @staticmethod
    def _to_match_query(query_text: str) -> str:
        tokens = [token.strip() for token in query_text.split() if token.strip()]
        if not tokens:
            return ""
        return " ".join(f'"{token.replace(chr(34), "")}"*' for token in tokens)

    @staticmethod
    def _row_to_result(row: sqlite3.Row) -> dict:
        labels = _normalize_labels(row["labels_json"] and json.loads(row["labels_json"]))
        return {
            "clip_key": row["clip_key"],
            "clip_item_id": row["clip_item_id"],
            "clip_name": row["clip_name"],
            "track_index": row["track_index"],
            "timeline_start_frame": row["timeline_start_frame"],
            "timeline_end_frame": row["timeline_end_frame"],
            "timeline_start_seconds": row["timeline_start_seconds"],
            "timeline_end_seconds": row["timeline_end_seconds"],
            "summary_text": row["summary_text"],
            "shot_type": row["shot_type"],
            "labels": labels,
            "ocr_text": row["ocr_text"] or "",
            "transcript_text": row["transcript_text"] or "",
            "error_state": row["error_state"],
            "error_message": row["error_message"],
            "match_fields": _format_snippets(row),
        }


class VisionProvider:
    provider_name = "vision"
    model_name = "unknown"

    async def analyze_clip(self, clip_context: dict, frame_paths: list[str]) -> dict:
        raise NotImplementedError


class GeminiVisionProvider(VisionProvider):
    provider_name = "gemini"
    model_name = "gemini-2.5-flash"

    def __init__(self):
        self._api_key = settings.gemini_api_key

    async def analyze_clip(self, clip_context: dict, frame_paths: list[str]) -> dict:
        return await asyncio.to_thread(self._analyze_clip_sync, clip_context, frame_paths)

    def _analyze_clip_sync(self, clip_context: dict, frame_paths: list[str]) -> dict:
        if not self._api_key:
            raise ValueError("Gemini API key not configured. Add one in Settings.")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("google-genai is not installed. Install backend requirements and retry.") from exc

        client = genai.Client(api_key=self._api_key)
        prompt = (
            "You are labeling one video clip for search and subtitle overlay.\n"
            "Describe only what is visually present in the frames.\n"
            "Return strict JSON with keys: summary, shot_type, objects, people, actions, scenes, locations, on_screen_text.\n"
            "Each non-summary field must be a short array of strings.\n"
            "summary must be one concise sentence under 18 words.\n"
            f"Clip name: {clip_context.get('clip_name') or 'Clip'}"
        )
        contents: list[Any] = [prompt]
        for frame_path in frame_paths:
            with open(frame_path, "rb") as handle:
                contents.append(types.Part.from_bytes(data=handle.read(), mime_type="image/jpeg"))

        response = client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        parsed = json.loads(raw_text) if raw_text else {}
        labels = _dedupe(
            _normalize_labels(parsed.get("objects"))
            + _normalize_labels(parsed.get("people"))
            + _normalize_labels(parsed.get("actions"))
            + _normalize_labels(parsed.get("scenes"))
            + _normalize_labels(parsed.get("locations"))
        )
        ocr_text = _normalize_labels(parsed.get("on_screen_text"))
        shot_type = str(parsed.get("shot_type") or "").strip()
        summary = _build_summary_text(str(parsed.get("summary") or "").strip(), shot_type, labels, ocr_text)
        return {
            "summary": summary,
            "shot_type": shot_type,
            "labels": labels,
            "ocr_text": ocr_text,
            "provider_payload": parsed,
        }


class ClipSearchService:
    def __init__(self, store: ClipSearchStore | None = None, frame_dir: str | Path | None = None):
        self._store = store
        self._store_is_external = store is not None
        self._frame_dir = Path(frame_dir) if frame_dir is not None else Path(settings.temp_dir) / "clip-search-frames"

    @property
    def store(self) -> ClipSearchStore:
        requested_path = _configured_db_path()
        if self._store_is_external and self._store is not None:
            return self._store
        if self._store is None or Path(self._store.requested_db_path) != requested_path:
            self._store = ClipSearchStore(db_path=requested_path)
        return self._store

    @property
    def frame_dir(self) -> Path:
        self._frame_dir.mkdir(parents=True, exist_ok=True)
        return self._frame_dir

    def get_storage_info(self) -> dict:
        configured_dir = _configured_storage_dir_path()
        effective_db_path = (
            str(self._store.db_path)
            if self._store is not None
            else str(configured_dir / "clip_search.sqlite3")
        )
        return {
            "configured_dir": str(configured_dir),
            "configured_db_path": str(configured_dir / "clip_search.sqlite3"),
            "using_custom_dir": bool(str(settings.clip_search_storage_dir or "").strip()),
            "effective_db_path": effective_db_path,
        }

    def refresh_storage_location(self, previous_db_path: str | Path | None = None) -> dict:
        target_db_path = _configured_db_path()
        source_db_path = Path(previous_db_path).expanduser() if previous_db_path else None
        migrated = False

        if source_db_path and source_db_path != target_db_path and source_db_path.exists() and not target_db_path.exists():
            target_db_path.parent.mkdir(parents=True, exist_ok=True)
            source_conn = sqlite3.connect(source_db_path)
            target_conn = sqlite3.connect(target_db_path)
            try:
                source_conn.backup(target_conn)
                migrated = True
            finally:
                with contextlib.suppress(Exception):
                    source_conn.close()
                with contextlib.suppress(Exception):
                    target_conn.close()

        self._store = None
        self._store_is_external = False
        storage = self.get_storage_info()
        storage["migrated"] = migrated
        return storage

    def _get_provider(self) -> VisionProvider:
        provider_name = str(settings.vision_provider or "gemini").strip().lower()
        if provider_name == "gemini":
            return GeminiVisionProvider()
        raise ValueError(f"Unsupported vision provider: {provider_name}")

    def _get_scope_info(self, track_index: int = 1) -> dict:
        timeline = resolve_service.get_current_timeline()
        if timeline is None:
            raise ValueError("No active timeline")

        fps_str = timeline.GetSetting("timelineFrameRate")
        fps = float(fps_str) if fps_str else 24.0
        timeline_start_frame = int(timeline.GetStartFrame())
        timeline_end_frame = int(timeline.GetEndFrame())
        in_out = resolve_service.get_in_out_range()
        if in_out is not None:
            scope_in_frame = int(in_out["in_frame"])
            scope_out_frame = int(in_out["out_frame"])
            scope_in_seconds = float(in_out["in_seconds"])
            scope_out_seconds = float(in_out["out_seconds"])
            scope_mode = "in_out"
        else:
            scope_in_frame = timeline_start_frame
            scope_out_frame = timeline_end_frame
            scope_in_seconds = 0.0
            scope_out_seconds = max(0.0, (timeline_end_frame - timeline_start_frame) / fps)
            scope_mode = "timeline"
        signature_info = resolve_service.get_timeline_signature()
        if signature_info is None:
            raise ValueError("No active timeline")

        project = resolve_service.get_current_project()
        project_name = None
        if project is not None:
            try:
                project_name = project.GetName()
            except Exception:
                project_name = None

        return {
            "project_name": project_name,
            "timeline_name": signature_info.get("timeline_name") or timeline.GetName(),
            "timeline_signature": str(signature_info.get("timeline_signature") or ""),
            "scope_in_frame": int(scope_in_frame),
            "scope_out_frame": int(scope_out_frame),
            "scope_in_seconds": float(scope_in_seconds),
            "scope_out_seconds": float(scope_out_seconds),
            "scope_mode": scope_mode,
            "track_index": int(track_index),
            "fps": fps,
            "timeline_start_frame": timeline_start_frame,
            "timeline_end_frame": timeline_end_frame,
        }

    def get_status(self, track_index: int = 1) -> dict:
        scope = self._get_scope_info(track_index=track_index)
        analysis_key = _build_analysis_key(
            scope["timeline_signature"],
            scope["scope_in_frame"],
            scope["scope_out_frame"],
            scope["track_index"],
        )
        transcript_map = self.store.load_transcript_map(scope["timeline_signature"])
        return {
            "analysis_key": analysis_key,
            "current_scope": {
                "mode": scope["scope_mode"],
                "track_index": scope["track_index"],
                "in_frame": scope["scope_in_frame"],
                "out_frame": scope["scope_out_frame"],
                "in_seconds": scope["scope_in_seconds"],
                "out_seconds": scope["scope_out_seconds"],
            },
            "analysis": self.store.get_status(analysis_key, current_timeline_signature=scope["timeline_signature"]),
            "transcript_available": bool(transcript_map),
            "vision_provider": settings.vision_provider,
            "has_provider_key": bool(settings.gemini_api_key) if settings.vision_provider == "gemini" else False,
        }

    async def analyze_current_timeline(
        self,
        *,
        track_index: int = 1,
        progress_cb=None,
    ) -> dict:
        scope = self._get_scope_info(track_index=track_index)
        analysis_key = _build_analysis_key(
            scope["timeline_signature"],
            scope["scope_in_frame"],
            scope["scope_out_frame"],
            scope["track_index"],
        )
        provider = self._get_provider()
        transcript_map = self.store.load_transcript_map(scope["timeline_signature"])
        self.store.start_analysis(
            analysis_key,
            project_name=scope["project_name"],
            timeline_name=scope["timeline_name"],
            timeline_signature=scope["timeline_signature"],
            scope_in_frame=scope["scope_in_frame"],
            scope_out_frame=scope["scope_out_frame"],
            track_index=scope["track_index"],
            scope_mode=scope["scope_mode"],
            provider_name=provider.provider_name,
            provider_model=provider.model_name,
        )

        selection = resolve_service.get_timeline_video_clip_contexts(
            track_index=scope["track_index"],
            in_frame=scope["scope_in_frame"],
            out_frame=scope["scope_out_frame"],
        )
        contexts = selection["contexts"]
        if not selection["has_any_items"]:
            self.store.fail_analysis(analysis_key, "No video clips found on the selected track.")
            raise ValueError("No video clips found on the selected track.")
        if not contexts:
            self.store.fail_analysis(analysis_key, "No usable video clips found in the selected scope.")
            raise ValueError("No usable video clips found in the selected scope.")

        if progress_cb:
            await progress_cb(0.05, f"Preparing {len(contexts)} clip(s)...")

        semaphore = asyncio.Semaphore(max(1, int(settings.vision_max_parallelism)))
        rows: list[dict] = []
        completed = 0
        progress_lock = asyncio.Lock()

        async def analyze_one(context: dict) -> dict:
            nonlocal completed
            clip_key = _build_clip_key_from_context(context)
            async with semaphore:
                try:
                    frame_paths = await asyncio.to_thread(self._extract_representative_frames, context)
                    provider_result = await provider.analyze_clip(context, frame_paths)
                    labels = _dedupe(provider_result.get("labels", []))
                    ocr_values = _normalize_labels(provider_result.get("ocr_text"))
                    row = {
                        "clip_key": clip_key,
                        "clip_item_id": context.get("clip_item_id"),
                        "clip_name": context.get("clip_name") or "Clip",
                        "track_index": context.get("track_index") or scope["track_index"],
                        "file_path": context.get("file_path"),
                        "timeline_start_frame": int(context["clip_start_frame"]),
                        "timeline_end_frame": int(context["clip_end_frame"]),
                        "timeline_start_seconds": float(context["clip_timeline_offset"]),
                        "timeline_end_seconds": float(context["clip_timeline_offset"]) + float(context["clip_duration_seconds"]),
                        "source_start_frame": context.get("source_start_frame"),
                        "source_end_frame": context.get("source_end_frame"),
                        "summary_text": _build_summary_text(
                            provider_result.get("summary", ""),
                            provider_result.get("shot_type", ""),
                            labels,
                            ocr_values,
                        ),
                        "shot_type": str(provider_result.get("shot_type") or "").strip(),
                        "labels": labels,
                        "labels_text": ", ".join(labels),
                        "ocr_text": " | ".join(ocr_values),
                        "transcript_text": transcript_map.get(clip_key, ""),
                        "provider_payload": provider_result.get("provider_payload") or {},
                        "error_state": None,
                        "error_message": None,
                        "analyzed_at": _utcnow_iso(),
                    }
                except Exception as exc:
                    logger.warning("Clip analysis failed for %s: %s", context.get("clip_name"), exc)
                    row = {
                        "clip_key": clip_key,
                        "clip_item_id": context.get("clip_item_id"),
                        "clip_name": context.get("clip_name") or "Clip",
                        "track_index": context.get("track_index") or scope["track_index"],
                        "file_path": context.get("file_path"),
                        "timeline_start_frame": int(context["clip_start_frame"]),
                        "timeline_end_frame": int(context["clip_end_frame"]),
                        "timeline_start_seconds": float(context["clip_timeline_offset"]),
                        "timeline_end_seconds": float(context["clip_timeline_offset"]) + float(context["clip_duration_seconds"]),
                        "source_start_frame": context.get("source_start_frame"),
                        "source_end_frame": context.get("source_end_frame"),
                        "summary_text": "Clip analysis failed",
                        "shot_type": "",
                        "labels": [],
                        "labels_text": "",
                        "ocr_text": "",
                        "transcript_text": transcript_map.get(clip_key, ""),
                        "provider_payload": {},
                        "error_state": "provider_error",
                        "error_message": str(exc),
                        "analyzed_at": _utcnow_iso(),
                    }

                async with progress_lock:
                    completed += 1
                    if progress_cb:
                        progress = 0.10 + (completed / max(1, len(contexts))) * 0.85
                        await progress_cb(progress, f"Analyzed {completed}/{len(contexts)} clips...")
                return row

        try:
            rows = await asyncio.gather(*(analyze_one(context) for context in contexts))
        except Exception as exc:
            self.store.fail_analysis(analysis_key, str(exc))
            raise

        self.store.complete_analysis(
            analysis_key,
            rows=rows,
            transcript_available=bool(transcript_map),
            provider_name=provider.provider_name,
            provider_model=provider.model_name,
        )
        return {
            "analysis_key": analysis_key,
            "clip_count": len(rows),
            "transcript_available": bool(transcript_map),
            "scope": {
                "mode": scope["scope_mode"],
                "track_index": scope["track_index"],
                "in_frame": scope["scope_in_frame"],
                "out_frame": scope["scope_out_frame"],
                "in_seconds": scope["scope_in_seconds"],
                "out_seconds": scope["scope_out_seconds"],
            },
            "results": self.store.query(analysis_key, "", limit=250),
        }

    def query_current_scope(self, query_text: str, *, track_index: int = 1, limit: int = 50) -> dict:
        scope = self._get_scope_info(track_index=track_index)
        analysis_key = _build_analysis_key(
            scope["timeline_signature"],
            scope["scope_in_frame"],
            scope["scope_out_frame"],
            scope["track_index"],
        )
        status = self.store.get_status(analysis_key, current_timeline_signature=scope["timeline_signature"])
        if status is None:
            return {
                "analysis_key": analysis_key,
                "results": [],
                "result_count": 0,
                "analysis": None,
                "transcript_available": bool(self.store.load_transcript_map(scope["timeline_signature"])),
                "message": "No clip analysis found for the current scope.",
            }
        results = self.store.query(analysis_key, query_text, limit=limit)
        return {
            "analysis_key": analysis_key,
            "results": results,
            "result_count": len(results),
            "analysis": status,
            "transcript_available": bool(self.store.load_transcript_map(scope["timeline_signature"])),
        }

    def build_overlay_srt(self, *, track_index: int = 1) -> tuple[str, int]:
        scope = self._get_scope_info(track_index=track_index)
        analysis_key = _build_analysis_key(
            scope["timeline_signature"],
            scope["scope_in_frame"],
            scope["scope_out_frame"],
            scope["track_index"],
        )
        rows = self.store.get_analysis_rows(analysis_key)
        if not rows:
            raise ValueError("No analyzed clips found for the current scope.")

        lines: list[str] = []
        for index, row in enumerate(rows, start=1):
            lines.append(str(index))
            lines.append(
                f"{seconds_to_srt_time(float(row['timeline_start_seconds']))} --> "
                f"{seconds_to_srt_time(float(row['timeline_end_seconds']))}"
            )
            lines.append(_format_overlay_text(row))
            lines.append("")
        return "\n".join(lines), len(rows)

    def clear_current_scope(self, *, track_index: int = 1) -> dict:
        scope = self._get_scope_info(track_index=track_index)
        analysis_key = _build_analysis_key(
            scope["timeline_signature"],
            scope["scope_in_frame"],
            scope["scope_out_frame"],
            scope["track_index"],
        )
        existing = self.store.get_status(analysis_key, current_timeline_signature=scope["timeline_signature"])
        deleted_clip_count = self.store.delete_analysis(analysis_key)
        return {
            "analysis_key": analysis_key,
            "cleared": existing is not None,
            "clip_count": deleted_clip_count,
            "scope": {
                "mode": scope["scope_mode"],
                "track_index": scope["track_index"],
                "in_frame": scope["scope_in_frame"],
                "out_frame": scope["scope_out_frame"],
                "in_seconds": scope["scope_in_seconds"],
                "out_seconds": scope["scope_out_seconds"],
            },
        }

    def persist_transcript_payload(
        self,
        timeline_signature: str,
        words: list[dict],
        *,
        replace_existing: bool = False,
    ) -> int:
        return self.store.persist_transcript_words(
            timeline_signature,
            words,
            replace_existing=replace_existing,
        )

    def _extract_representative_frames(self, context: dict) -> list[str]:
        file_path = str(context.get("file_path") or "").strip()
        if not file_path:
            raise ValueError("Clip is missing a source file path.")

        source_start_seconds = context.get("source_start_seconds")
        source_end_seconds = context.get("source_end_seconds")
        if source_start_seconds is None or source_end_seconds is None:
            source_start_seconds = 0.0
            source_end_seconds = float(context.get("clip_duration_seconds") or 0.0)

        start_seconds = float(source_start_seconds)
        end_seconds = float(source_end_seconds)
        if end_seconds <= start_seconds:
            end_seconds = start_seconds + max(0.1, float(context.get("clip_duration_seconds") or 0.1))

        duration = end_seconds - start_seconds
        ratios = [0.5] if duration < 0.75 else [0.2, 0.5, 0.8]
        frame_paths: list[str] = []
        for ratio in ratios:
            sample_seconds = start_seconds + duration * ratio
            raw = ":".join(
                [
                    file_path,
                    str(context.get("clip_item_id") or ""),
                    f"{sample_seconds:.4f}",
                ]
            )
            digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
            output_path = self.frame_dir / f"{_slug(context.get('clip_name') or 'clip')}_{digest}.jpg"
            if not output_path.exists():
                extract_frame(file_path, sample_seconds, output_path=str(output_path), width=1280)
            frame_paths.append(str(output_path))
        return frame_paths


clip_search_service = ClipSearchService()
