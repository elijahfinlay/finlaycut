from pydantic import BaseModel


class SilenceConfig(BaseModel):
    threshold_db: int = -40
    min_duration_ms: int = 300
    padding_ms: int = 100


class SilenceSegment(BaseModel):
    start: float
    end: float
    duration: float
    selected: bool = True


class SilenceAnalysisResult(BaseModel):
    segments: list[SilenceSegment]
    total_silence_seconds: float
    total_duration_seconds: float
    audio_path: str


class SilenceApplyRequest(BaseModel):
    segments: list[SilenceSegment]
    padding_ms: int = 100
