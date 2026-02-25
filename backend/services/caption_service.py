"""Caption/subtitle generation service."""

from utils.time_utils import seconds_to_srt_time


class CaptionService:
    def generate_srt(self, transcript: dict, max_chars_per_line: int = 42) -> str:
        """Generate SRT subtitle content from a transcript."""
        segments = transcript.get("segments", [])
        srt_lines = []

        for i, segment in enumerate(segments, 1):
            start = seconds_to_srt_time(segment["start"])
            end = seconds_to_srt_time(segment["end"])
            text = segment["text"].strip()

            # Word-wrap long lines
            if len(text) > max_chars_per_line:
                words = text.split()
                lines = []
                current_line = ""
                for word in words:
                    if len(current_line) + len(word) + 1 > max_chars_per_line:
                        lines.append(current_line.strip())
                        current_line = word
                    else:
                        current_line += " " + word
                if current_line.strip():
                    lines.append(current_line.strip())
                text = "\n".join(lines)

            srt_lines.append(f"{i}")
            srt_lines.append(f"{start} --> {end}")
            srt_lines.append(text)
            srt_lines.append("")

        return "\n".join(srt_lines)

    def generate_word_level_captions(
        self,
        transcript: dict,
        words_per_caption: int = 5,
    ) -> list[dict]:
        """
        Generate word-level caption entries for animated captions.

        Returns list of caption entries, each with:
            - start, end: timing in seconds
            - words: list of {word, start, end, highlight_start, highlight_end}
            - text: combined text
        """
        words = transcript.get("words", [])
        if not words:
            return []

        captions = []
        for i in range(0, len(words), words_per_caption):
            chunk = words[i:i + words_per_caption]
            if not chunk:
                break

            caption = {
                "start": chunk[0]["start"],
                "end": chunk[-1]["end"],
                "text": " ".join(w["word"] for w in chunk),
                "words": [
                    {
                        "word": w["word"],
                        "start": w["start"],
                        "end": w["end"],
                    }
                    for w in chunk
                ]
            }
            captions.append(caption)

        return captions

    def generate_youtube_chapters(self, chapters: list[dict]) -> str:
        """Generate YouTube-formatted chapter timestamps."""
        lines = []
        for ch in chapters:
            seconds = ch.get("start_seconds", 0)
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            title = ch.get("title", "Chapter")
            lines.append(f"{minutes}:{secs:02d} {title}")
        return "\n".join(lines)


caption_service = CaptionService()
