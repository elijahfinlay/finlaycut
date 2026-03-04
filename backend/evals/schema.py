"""Pydantic models for fixture-driven quality evaluation."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class IntervalExpectation(BaseModel):
    start: float
    end: float
    label: str | None = None
    kind: str | None = None
    optional: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "IntervalExpectation":
        if float(self.end) <= float(self.start):
            raise ValueError("end must be greater than start")
        return self


class SpeakerSegmentExpectation(BaseModel):
    start: float
    end: float
    speaker: str
    optional: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "SpeakerSegmentExpectation":
        if float(self.end) <= float(self.start):
            raise ValueError("end must be greater than start")
        return self


class WordSpeakerExpectation(BaseModel):
    word_index: int = Field(ge=0)
    speaker: str
    optional: bool = False


class FillerEvaluationConfig(BaseModel):
    enabled: bool = False
    filler_words: list[str] | None = None
    match_tolerance_ms: int = Field(140, ge=0, le=2000)
    minimum_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    expected: list[IntervalExpectation] = Field(default_factory=list)


class SilenceEvaluationConfig(BaseModel):
    enabled: bool = False
    threshold_db: int = -40
    min_duration_ms: int = Field(300, ge=1, le=60000)
    min_duration_frames: int = Field(14, ge=14, le=240)
    match_tolerance_ms: int = Field(160, ge=0, le=2000)
    minimum_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    expected: list[IntervalExpectation] = Field(default_factory=list)


class SpeakerEvaluationConfig(BaseModel):
    enabled: bool = False
    num_speakers: int | None = Field(default=None, ge=1, le=10)
    minimum_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    expected_segments: list[SpeakerSegmentExpectation] = Field(default_factory=list)
    expected_word_speakers: list[WordSpeakerExpectation] = Field(default_factory=list)


class EvaluationFixture(BaseModel):
    id: str
    audio_path: str
    enabled: bool = True
    fps: float = Field(24.0, gt=0.0, le=240.0)
    language: str | None = None
    notes: str | None = None
    transcript_path: str | None = None
    transcript: dict | None = None
    filler: FillerEvaluationConfig = Field(default_factory=FillerEvaluationConfig)
    silence: SilenceEvaluationConfig = Field(default_factory=SilenceEvaluationConfig)
    speakers: SpeakerEvaluationConfig = Field(default_factory=SpeakerEvaluationConfig)

    @model_validator(mode="after")
    def validate_fixture(self) -> "EvaluationFixture":
        enabled_tasks = []
        if self.filler.enabled:
            enabled_tasks.append("filler")
            if not self.filler.expected:
                raise ValueError("filler.enabled requires filler.expected")
        if self.silence.enabled:
            enabled_tasks.append("silence")
            if not self.silence.expected:
                raise ValueError("silence.enabled requires silence.expected")
        if self.speakers.enabled:
            enabled_tasks.append("speakers")
            if not self.speakers.expected_segments and not self.speakers.expected_word_speakers:
                raise ValueError(
                    "speakers.enabled requires speakers.expected_segments or speakers.expected_word_speakers"
                )
        if not enabled_tasks:
            raise ValueError("fixture must enable at least one evaluation task")
        return self
