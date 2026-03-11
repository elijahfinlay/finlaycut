"""
AI service: abstracts over Ollama (local, free) and Claude API (optional).
Handles chapter detection, viral scoring, B-roll suggestions, etc.
"""

import json
import logging
import re
from typing import Optional
import httpx
from config import settings

logger = logging.getLogger("finlaycut.ai")

# Repetition detection constants
_REPETITION_MIN_MATCH_WORDS = 3
_REPETITION_PROXIMITY_SECONDS = 30.0
_REPETITION_MIN_PAUSE_SECONDS = 0.3
_FILLER_WORDS = {"um", "uh", "uh-huh", "uhh", "uhm", "umm", "hmm", "hm", "mmhmm",
                 "mhm", "mm", "mmm", "huh", "er", "erm", "ah", "ahh", "like", "you know"}


class AIService:
    def __init__(self):
        self._backend = settings.ai_backend

    @property
    def backend(self) -> str:
        return self._backend

    def set_backend(self, backend: str):
        if backend not in ("ollama", "claude"):
            raise ValueError(f"Invalid AI backend: {backend}")
        self._backend = backend

    async def _call_ollama(self, prompt: str, system: str = "") -> str:
        """Call local Ollama for inference."""
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
            }
            if system:
                payload["system"] = system

            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")

    async def _call_claude(self, prompt: str, system: str = "") -> str:
        """Call Claude API for inference."""
        if not settings.claude_api_key:
            raise ValueError("Claude API key not configured. Set it in Settings.")

        async with httpx.AsyncClient(timeout=120.0) as client:
            messages = [{"role": "user", "content": prompt}]
            payload = {
                "model": settings.claude_model,
                "max_tokens": 4096,
                "messages": messages,
            }
            if system:
                payload["system"] = system

            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.claude_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"]

    async def generate(self, prompt: str, system: str = "") -> str:
        """Generate text using the configured AI backend."""
        if self._backend == "claude" and settings.claude_api_key:
            return await self._call_claude(prompt, system)
        return await self._call_ollama(prompt, system)

    async def detect_chapters(self, transcript_text: str) -> list[dict]:
        """
        Use AI to detect natural chapter boundaries in a transcript.
        Returns list of {title, start_seconds, summary}.
        """
        system = "You are a video editing assistant. Analyze transcripts and identify natural topic changes."
        prompt = f"""Analyze this transcript and identify natural chapter/topic boundaries.

For each chapter, provide:
- title: A short, descriptive title (2-6 words)
- approximate_position: What percentage through the transcript this chapter starts (0-100)
- summary: One sentence describing what this section covers

Return your response as a JSON array of objects.

Transcript:
{transcript_text[:8000]}

Return ONLY valid JSON, no other text."""

        response = await self.generate(prompt, system)

        try:
            # Try to extract JSON from response
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            chapters = json.loads(response)
            return chapters
        except json.JSONDecodeError:
            logger.warning("Failed to parse chapters response: %s", response[:200])
            return []

    async def score_viral_potential(self, transcript_text: str, duration_seconds: float) -> list[dict]:
        """
        Score segments of the transcript for viral potential.
        Returns list of {start_pct, end_pct, score, reason, suggested_title}.
        """
        system = "You are a social media content strategist who identifies viral-worthy clips."
        prompt = f"""Analyze this transcript from a {duration_seconds:.0f}-second video.
Identify the top 5 segments (15-60 seconds each) that would make the best short-form clips (TikTok/Reels/Shorts).

For each clip:
- start_pct: approximate start position (0-100% through the video)
- end_pct: approximate end position
- score: viral potential score (1-10)
- reason: why this would perform well
- suggested_title: a catchy title for the clip

Return ONLY valid JSON array, no other text.

Transcript:
{transcript_text[:8000]}"""

        response = await self.generate(prompt, system)

        try:
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            clips = json.loads(response)
            return clips
        except json.JSONDecodeError:
            return []

    async def suggest_broll(self, transcript_text: str) -> list[dict]:
        """
        Suggest B-roll search terms based on transcript content.
        Returns list of {time_pct, search_term, context, reason}.
        """
        system = "You are a video editor who suggests relevant B-roll footage."
        prompt = f"""Analyze this transcript and suggest where B-roll footage would enhance the video.

For each suggestion:
- time_pct: approximate position (0-100% through the video)
- search_term: a search query for stock footage (Pexels/Pixabay)
- context: the transcript text at this point
- reason: why B-roll would help here

Return ONLY valid JSON array. Suggest 5-10 B-roll opportunities.

Transcript:
{transcript_text[:8000]}"""

        response = await self.generate(prompt, system)

        try:
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            return json.loads(response)
        except json.JSONDecodeError:
            return []

    # ------------------------------------------------------------------
    # Repetition detection — word-level n-gram matching + heuristic scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_word(w: str) -> str:
        """Lowercase + strip punctuation for comparison."""
        return re.sub(r"[^a-z']", "", w.lower())

    @staticmethod
    def _build_word_index(words: list[dict]) -> dict[str, list[int]]:
        """Inverted index: normalized word → list of positions."""
        index: dict[str, list[int]] = {}
        for pos, w in enumerate(words):
            norm = AIService._normalize_word(w.get("word", ""))
            if norm:
                index.setdefault(norm, []).append(pos)
        return index

    @staticmethod
    def _word_text(word: dict) -> str:
        return str(word.get("word", "")).strip()

    def _build_segments_from_words(self, words: list[dict]) -> list[dict]:
        """Infer simple pause-delimited segments when the transcript has no segments."""
        if not words:
            return []

        segments: list[dict] = []
        start_idx = 0
        for idx in range(1, len(words)):
            gap = words[idx].get("start", 0) - words[idx - 1].get("end", 0)
            if gap < _REPETITION_MIN_PAUSE_SECONDS:
                continue
            segment_words = words[start_idx:idx]
            if segment_words:
                segments.append({
                    "start": segment_words[0].get("start", 0),
                    "end": segment_words[-1].get("end", 0),
                    "text": " ".join(self._word_text(word) for word in segment_words).strip(),
                })
            start_idx = idx

        segment_words = words[start_idx:]
        if segment_words:
            segments.append({
                "start": segment_words[0].get("start", 0),
                "end": segment_words[-1].get("end", 0),
                "text": " ".join(self._word_text(word) for word in segment_words).strip(),
            })
        return segments

    def _common_prefix_tokens(self, sequences: list[list[str]]) -> list[str]:
        """Return the normalized common prefix across multiple token sequences."""
        if not sequences:
            return []

        prefix = list(sequences[0])
        for sequence in sequences[1:]:
            limit = min(len(prefix), len(sequence))
            next_prefix: list[str] = []
            for idx in range(limit):
                if self._normalize_word(prefix[idx]) != self._normalize_word(sequence[idx]):
                    break
                next_prefix.append(prefix[idx])
            prefix = next_prefix
            if len(prefix) < _REPETITION_MIN_MATCH_WORDS:
                return prefix
        return prefix

    def _find_match_start_in_take(
        self,
        words: list[dict],
        take_start: int,
        take_end: int,
        matched_tokens: list[str],
    ) -> int:
        """Find the first occurrence of matched tokens inside a take."""
        normalized_tokens = [self._normalize_word(token) for token in matched_tokens]
        normalized_tokens = [token for token in normalized_tokens if token]
        if not normalized_tokens:
            return take_start

        max_start = take_end - len(normalized_tokens)
        for start in range(take_start, max_start + 1):
            for offset, token in enumerate(normalized_tokens):
                if self._normalize_word(words[start + offset].get("word", "")) != token:
                    break
            else:
                return start
        return take_start

    def _find_matching_sequences(
        self, words: list[dict], word_index: dict[str, list[int]]
    ) -> list[dict]:
        """
        Find repeated word sequences and deduplicate anchors that land on the
        same pair of pause-delimited takes.
        """
        raw_matches: list[tuple[int, int, int]] = []

        for norm, positions in word_index.items():
            # Skip single-char words as anchors ("I", "a") — too noisy
            if len(norm) <= 1:
                continue
            if len(positions) < 2:
                continue

            for pi in range(len(positions)):
                for pj in range(pi + 1, len(positions)):
                    i, j = positions[pi], positions[pj]
                    # Enforce proximity window
                    t_i = words[i].get("start", 0)
                    t_j = words[j].get("start", 0)
                    if abs(t_j - t_i) > _REPETITION_PROXIMITY_SECONDS:
                        continue

                    # Extend forward to find longest match
                    k = 0
                    while (
                        i + k < j  # prevent overlap
                        and j + k < len(words)
                        and self._normalize_word(words[i + k].get("word", ""))
                        == self._normalize_word(words[j + k].get("word", ""))
                        and self._normalize_word(words[i + k].get("word", ""))  # non-empty
                    ):
                        k += 1

                    if k >= _REPETITION_MIN_MATCH_WORDS:
                        raw_matches.append((i, j, k))

        raw_matches.sort(key=lambda match: (-match[2], match[0], match[1]))
        pair_matches: dict[tuple[int, int, int, int], dict] = {}
        for i, j, k in raw_matches:
            take_a_start, take_a_end = self._determine_take_boundaries(words, i, k)
            take_b_start, take_b_end = self._determine_take_boundaries(words, j, k)
            if (take_a_start, take_a_end) == (take_b_start, take_b_end):
                continue

            key = (take_a_start, take_a_end, take_b_start, take_b_end)
            existing = pair_matches.get(key)
            if existing is not None and existing["match_length"] >= k:
                continue

            pair_matches[key] = {
                "take_a": (take_a_start, take_a_end),
                "take_b": (take_b_start, take_b_end),
                "match_length": k,
                "matched_tokens": [
                    self._word_text(words[i + offset])
                    for offset in range(k)
                ],
            }

        return sorted(
            pair_matches.values(),
            key=lambda match: (
                -match["match_length"],
                match["take_a"][0],
                match["take_b"][0],
            ),
        )

    def _determine_take_boundaries(
        self, words: list[dict], pos: int, match_len: int
    ) -> tuple[int, int]:
        """
        Extend a match occurrence to the nearest pause boundary.
        Returns (start_index, end_index_exclusive).
        """
        # Extend backward
        start = pos
        while start > 0:
            gap = words[start].get("start", 0) - words[start - 1].get("end", 0)
            if gap >= _REPETITION_MIN_PAUSE_SECONDS:
                break
            start -= 1

        # Extend forward
        end = pos + match_len
        while end < len(words):
            gap = words[end].get("start", 0) - words[end - 1].get("end", 0)
            if gap >= _REPETITION_MIN_PAUSE_SECONDS:
                break
            end += 1

        return start, end

    def _score_take(
        self,
        words: list[dict],
        take_start: int,
        take_end: int,
        match_start: int,
        match_len: int,
        segments: list[dict],
    ) -> dict:
        """
        Score a take on completion, confidence, and fluency.
        Returns dict with individual scores and combined.
        """
        take_words = words[take_start:take_end]
        if not take_words:
            return {"completion": 0, "confidence": 0, "fluency": 0, "combined": 0}

        # Completion: how much continues after the matched prefix
        words_after_match = take_end - (match_start + match_len)
        completion = min(1.0, words_after_match / max(match_len, 1))
        # Bonus if take ends near a segment boundary (sentence completion)
        take_end_time = take_words[-1].get("end", 0)
        for seg in segments:
            if abs(seg.get("end", 0) - take_end_time) < 0.15:
                completion = min(1.0, completion + 0.3)
                break

        # Confidence: average WhisperX probability
        probs = [w.get("probability", 0.5) for w in take_words]
        confidence = sum(probs) / len(probs) if probs else 0.5

        # Fluency: penalize fillers
        word_count = len(take_words)
        filler_count = sum(
            1 for w in take_words
            if self._normalize_word(w.get("word", "")) in _FILLER_WORDS
        )
        fluency = max(0.0, 1.0 - (filler_count / max(word_count, 1)) * 2.0)

        combined = 0.5 * completion + 0.3 * confidence + 0.2 * fluency

        return {
            "completion": round(completion, 3),
            "confidence": round(confidence, 3),
            "fluency": round(fluency, 3),
            "combined": round(combined, 3),
        }

    async def detect_repetitions(self, transcript: dict) -> list[dict]:
        """
        Detect repeated takes/phrases using word-level n-gram matching.
        Scores each take on completion, confidence, and fluency.
        Returns groups with scored takes and best_index recommendation.
        """
        words = transcript.get("words", [])
        segments = transcript.get("segments") or []

        if len(words) < 6:
            return []
        if not segments:
            segments = self._build_segments_from_words(words)

        word_index = self._build_word_index(words)
        matches = self._find_matching_sequences(words, word_index)
        if not matches:
            return []

        components: list[dict] = []
        for match in matches:
            take_a = match["take_a"]
            take_b = match["take_b"]
            match_tokens = match["matched_tokens"]

            comp_a = None
            comp_b = None
            for idx, component in enumerate(components):
                if take_a in component["takes"]:
                    comp_a = idx
                if take_b in component["takes"]:
                    comp_b = idx

            if comp_a is None and comp_b is None:
                components.append({
                    "takes": {take_a, take_b},
                    "matched_tokens": match_tokens,
                })
                continue

            if comp_a is not None and comp_b is not None and comp_a == comp_b:
                merged_tokens = self._common_prefix_tokens([
                    components[comp_a]["matched_tokens"],
                    match_tokens,
                ])
                if len(merged_tokens) >= _REPETITION_MIN_MATCH_WORDS:
                    components[comp_a]["matched_tokens"] = merged_tokens
                continue

            component_indices = [idx for idx in (comp_a, comp_b) if idx is not None]
            combined_takes: set[tuple[int, int]] = {take_a, take_b}
            token_sequences: list[list[str]] = [match_tokens]
            for idx in component_indices:
                combined_takes |= components[idx]["takes"]
                token_sequences.append(components[idx]["matched_tokens"])

            merged_tokens = self._common_prefix_tokens(token_sequences)
            if len(merged_tokens) < _REPETITION_MIN_MATCH_WORDS:
                continue

            target_idx = component_indices[0] if component_indices else len(components)
            if component_indices:
                components[target_idx]["takes"] = combined_takes
                components[target_idx]["matched_tokens"] = merged_tokens
                for idx in component_indices[1:]:
                    components[idx]["takes"] = set()
                    components[idx]["matched_tokens"] = []
            else:
                components.append({
                    "takes": combined_takes,
                    "matched_tokens": merged_tokens,
                })

        groups: list[dict] = []
        for component in components:
            take_ranges = sorted(component["takes"], key=lambda take: take[0])
            matched_tokens = component["matched_tokens"]
            if len(take_ranges) < 2 or len(matched_tokens) < _REPETITION_MIN_MATCH_WORDS:
                continue

            matched_text = " ".join(matched_tokens)
            match_len = len(matched_tokens)
            takes = []
            for start_idx, end_idx in take_ranges:
                match_start = self._find_match_start_in_take(
                    words, start_idx, end_idx, matched_tokens,
                )
                take_text = " ".join(
                    self._word_text(word) for word in words[start_idx:end_idx]
                ).strip()
                scores = self._score_take(
                    words, start_idx, end_idx, match_start, match_len, segments,
                )
                prev_word = words[start_idx - 1] if start_idx > 0 else None
                next_word = words[end_idx] if end_idx < len(words) else None
                takes.append({
                    "text": take_text,
                    "start": words[start_idx].get("start", 0),
                    "end": words[end_idx - 1].get("end", 0),
                    "start_frame": words[start_idx].get("start_frame"),
                    "end_frame": words[end_idx - 1].get("end_frame"),
                    "source_start_frame": words[start_idx].get("source_start_frame"),
                    "source_end_frame": words[end_idx - 1].get("source_end_frame"),
                    "prev_word_end": prev_word.get("end") if prev_word is not None else None,
                    "prev_word_end_frame": prev_word.get("end_frame") if prev_word is not None else None,
                    "next_word_start": next_word.get("start") if next_word is not None else None,
                    "next_word_start_frame": next_word.get("start_frame") if next_word is not None else None,
                    "word_start_index": start_idx,
                    "word_end_index": end_idx,
                    "scores": scores,
                })

            best_index = max(
                range(len(takes)),
                key=lambda idx: takes[idx]["scores"]["combined"],
            )

            groups.append({
                "takes": takes,
                "best_index": best_index,
                "matched_text": matched_text,
                "match_length": match_len,
                "count": len(takes),
            })

        # Sort by timeline position
        groups.sort(key=lambda g: g["takes"][0]["start"])
        return groups

    async def check_ollama_available(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{settings.ollama_base_url}/api/tags")
                return response.status_code == 200
        except Exception:
            return False


ai_service = AIService()
