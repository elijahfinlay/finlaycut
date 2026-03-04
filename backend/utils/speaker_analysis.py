"""Shared helpers for speaker loudness and summary generation."""

from __future__ import annotations

import logging
from collections import defaultdict

import numpy as np

logger = logging.getLogger("finlaycut.speaker_analysis")


def compute_speaker_loudness(audio_path: str, segments: list[dict]) -> dict[str, dict]:
    try:
        import librosa
    except Exception:
        return {}

    try:
        y, sr = librosa.load(audio_path, sr=16000, mono=True)
    except Exception as e:
        logger.warning("Could not load audio for speaker loudness: %s", e)
        return {}

    if y.size == 0:
        return {}

    aggregates: dict[str, dict] = defaultdict(lambda: {
        "weighted_db_sum": 0.0,
        "duration_sum": 0.0,
        "peak_db": None,
    })

    for seg in segments:
        speaker = seg.get("speaker")
        if not speaker:
            continue
        start = max(0.0, float(seg.get("start", 0.0)))
        end = max(start, float(seg.get("end", start)))
        start_idx = min(len(y), int(round(start * sr)))
        end_idx = min(len(y), int(round(end * sr)))
        if end_idx <= start_idx:
            continue

        samples = y[start_idx:end_idx]
        if samples.size == 0:
            continue

        rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
        peak = float(np.max(np.abs(samples)))
        avg_db = 20.0 * np.log10(max(rms, 1e-6))
        peak_db = 20.0 * np.log10(max(peak, 1e-6))
        duration = end - start

        bucket = aggregates[speaker]
        bucket["weighted_db_sum"] += avg_db * duration
        bucket["duration_sum"] += duration
        bucket["peak_db"] = peak_db if bucket["peak_db"] is None else max(bucket["peak_db"], peak_db)

    results: dict[str, dict] = {}
    for speaker, stats in aggregates.items():
        duration_sum = max(stats["duration_sum"], 1e-6)
        results[speaker] = {
            "avg_dbfs": round(stats["weighted_db_sum"] / duration_sum, 2),
            "peak_dbfs": round(stats["peak_db"], 2) if stats["peak_db"] is not None else None,
        }
    return results


def build_speaker_summaries(segments: list[dict], words: list[dict], loudness: dict[str, dict]) -> list[dict]:
    speaker_stats: dict[str, dict] = {}

    for seg in segments:
        speaker = seg.get("speaker")
        if not speaker:
            continue
        entry = speaker_stats.setdefault(speaker, {
            "speaker": speaker,
            "speaker_index": seg.get("speaker_index"),
            "total_duration": 0.0,
            "segment_count": 0,
            "word_count": 0,
        })
        entry["total_duration"] += max(0.0, float(seg.get("duration", 0.0)))
        entry["segment_count"] += 1

    for word in words:
        speaker = word.get("speaker")
        if not speaker:
            continue
        entry = speaker_stats.setdefault(speaker, {
            "speaker": speaker,
            "speaker_index": word.get("speaker_index"),
            "total_duration": 0.0,
            "segment_count": 0,
            "word_count": 0,
        })
        entry["word_count"] += 1
        if entry.get("speaker_index") is None:
            entry["speaker_index"] = word.get("speaker_index")

    if not speaker_stats:
        return []

    max_duration = max(entry["total_duration"] for entry in speaker_stats.values()) or 1.0
    loudest_db = max(
        loudness.get(speaker, {}).get("avg_dbfs", -80.0)
        for speaker in speaker_stats
    )

    summaries = []
    for speaker, entry in speaker_stats.items():
        avg_db = loudness.get(speaker, {}).get("avg_dbfs")
        peak_db = loudness.get(speaker, {}).get("peak_dbfs")
        loudness_weight = 10 ** (((avg_db if avg_db is not None else loudest_db) - loudest_db) / 20.0)
        score = 0.65 * (entry["total_duration"] / max_duration) + 0.35 * loudness_weight
        summaries.append({
            "speaker": speaker,
            "speaker_index": entry.get("speaker_index"),
            "total_duration": round(entry["total_duration"], 2),
            "segment_count": int(entry["segment_count"]),
            "word_count": int(entry["word_count"]),
            "avg_dbfs": avg_db,
            "peak_dbfs": peak_db,
            "relative_db": round((avg_db - loudest_db), 2) if avg_db is not None else None,
            "selection_score": round(score, 4),
        })

    summaries.sort(key=lambda entry: (-entry["selection_score"], entry["speaker"]))
    if summaries:
        primary = summaries[0]["speaker"]
        for entry in summaries:
            entry["is_primary_suggestion"] = entry["speaker"] == primary

    return summaries
