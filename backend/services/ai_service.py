"""
AI service: abstracts over Ollama (local, free) and Claude API (optional).
Handles chapter detection, viral scoring, B-roll suggestions, etc.
"""

import json
from typing import Optional
import httpx
from config import settings


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
                "model": "claude-sonnet-4-20250514",
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
            print(f"[AIService] Failed to parse chapters response: {response[:200]}")
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

    async def detect_repetitions(self, transcript: dict) -> list[dict]:
        """
        Detect repeated takes/phrases in transcript.
        Returns groups of similar segments.
        """
        import difflib

        segments = transcript.get("segments", [])
        groups = []
        used = set()

        for i, seg_a in enumerate(segments):
            if i in used:
                continue
            similar = [{"index": i, "text": seg_a["text"], "start": seg_a["start"], "end": seg_a["end"]}]

            for j, seg_b in enumerate(segments[i + 1:], start=i + 1):
                if j in used:
                    continue
                ratio = difflib.SequenceMatcher(None, seg_a["text"].lower(), seg_b["text"].lower()).ratio()
                if ratio > 0.6:  # 60% similarity threshold
                    similar.append({"index": j, "text": seg_b["text"], "start": seg_b["start"], "end": seg_b["end"]})
                    used.add(j)

            if len(similar) > 1:
                used.add(i)
                groups.append({
                    "takes": similar,
                    "count": len(similar),
                    "best_index": 0  # Default: first take. AI can improve this.
                })

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
