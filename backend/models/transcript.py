from pydantic import BaseModel
from typing import Optional


class Word(BaseModel):
    word: str
    start: float
    end: float
    probability: float = 1.0


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
    context: str
    selected: bool = True


class TranscribeRequest(BaseModel):
    language: Optional[str] = None
    model: Optional[str] = None
