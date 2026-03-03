from pydantic import BaseModel
from typing import Optional


class CaptionStyle(BaseModel):
    font: str = "Arial"
    font_size: int = 48
    color: str = "#FFFFFF"
    background_color: Optional[str] = "#000000CC"
    position: str = "bottom"  # "top", "center", "bottom"
    bold: bool = True
    italic: bool = False
    outline_color: Optional[str] = "#000000"
    outline_width: int = 2
    animation: str = "none"  # "none", "word_highlight", "karaoke"
    highlight_color: str = "#FFD700"


class CaptionEntry(BaseModel):
    start: float
    end: float
    text: str
    words: list[dict] = []


class CaptionGenerateRequest(BaseModel):
    language: Optional[str] = None
    style: CaptionStyle = CaptionStyle()
    words_per_caption: int = 5
    max_chars_per_line: int = 42


class CaptionApplyRequest(BaseModel):
    captions: list[CaptionEntry]
    style: CaptionStyle = CaptionStyle()
    srt: Optional[str] = None
    use_fusion: bool = False
