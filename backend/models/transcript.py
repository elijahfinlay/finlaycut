from pydantic import BaseModel
from typing import Optional


class Word(BaseModel):
    word: str
    start: float
    end: float
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    source_start_frame: Optional[int] = None
    source_end_frame: Optional[int] = None
    speaker: Optional[str] = None
    speaker_index: Optional[int] = None
    probability: float = 1.0
    alignment_confidence: Optional[float] = None  # CTC score 0-1
    alignment_method: Optional[str] = None        # "mms_fa" | "whisperx" | "fallback"
    boundary_refined: Optional[bool] = None       # True if waveform-refined


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = []


class Transcript(BaseModel):
    text: str
    language: Optional[str] = None
    segments: list[Segment] = []
    words: list[Word] = []


class FillerWordDetection(BaseModel):
    word: str
    start: float
    end: float
    start_frame: Optional[int] = None
    end_frame: Optional[int] = None
    source_start_frame: Optional[int] = None
    source_end_frame: Optional[int] = None
    context: str
    boundary_source: Optional[str] = None
    selected: bool = True


class TranscribeRequest(BaseModel):
    language: Optional[str] = None
    model: Optional[str] = None
