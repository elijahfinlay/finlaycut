from pydantic import BaseModel, Field


class SilenceConfig(BaseModel):
    threshold_db: int = -40
    min_duration_ms: int = 300
    min_duration_frames: int = Field(14, ge=14, le=240)
    padding_ms: int = 100


class SilenceSegment(BaseModel):
    start: float
    end: float
    duration: float
    start_frame: int | None = None
    end_frame: int | None = None
    kind: str | None = None
    boundary_source: str | None = None
    selected: bool = True


class SilenceAnalysisResult(BaseModel):
    segments: list[SilenceSegment]
    total_silence_seconds: float
    total_duration_seconds: float
    audio_path: str


class SilenceApplyRequest(BaseModel):
    segments: list[SilenceSegment]
    padding_ms: int = 100
