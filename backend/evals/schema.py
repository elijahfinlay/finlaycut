"""Evaluation fixture schema used by the offline quality runner."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class WordAlignmentExpectation(EvalModel):
    """Ground-truth word boundary for a single word."""

    word: str
    expected_start: float
    expected_end: float
    tolerance_ms: float = 66.0


class AlignmentEvaluationConfig(EvalModel):
    """Configuration for word-boundary accuracy checks."""

    audio_path: str
    language: str = "en"
    word_expectations: list[WordAlignmentExpectation] = Field(default_factory=list)
    global_tolerance_ms: Optional[float] = None
    target_median_error_ms: float = 15.0
    target_pct_within_1_frame: float = 85.0
    target_pct_within_2_frames: float = 95.0


class IntervalExpectation(EvalModel):
    start: float
    end: float
    label: Optional[str] = None
    kind: Optional[str] = None
    speaker: Optional[str] = None
    notes: Optional[str] = None


class FillerEvaluationConfig(EvalModel):
    enabled: bool = False
    filler_words: Optional[list[str]] = None
    match_tolerance_ms: float = 140.0
    minimum_f1: Optional[float] = None
    expected: list[IntervalExpectation] = Field(default_factory=list)


class SilenceEvaluationConfig(EvalModel):
    enabled: bool = False
    threshold_db: int = -40
    min_duration_ms: int = 300
    min_duration_frames: int = 14
    match_tolerance_ms: float = 160.0
    minimum_f1: Optional[float] = None
    expected: list[IntervalExpectation] = Field(default_factory=list)


class SpeakerSegmentExpectation(EvalModel):
    start: float
    end: float
    speaker: str
    notes: Optional[str] = None


class WordSpeakerExpectation(EvalModel):
    word: str
    start: float
    end: float
    speaker: str


class SpeakerEvaluationConfig(EvalModel):
    enabled: bool = False
    num_speakers: Optional[int] = None
    minimum_accuracy: Optional[float] = None
    expected_segments: list[SpeakerSegmentExpectation] = Field(default_factory=list)
    expected_word_speakers: list[WordSpeakerExpectation] = Field(default_factory=list)


class EvaluationFixture(EvalModel):
    """A complete quality-eval fixture."""

    id: str
    enabled: bool = True
    audio_path: str
    fps: float = 24.0
    language: str = "en"
    notes: str = ""
    transcript_path: Optional[str] = None
    transcript: Optional[dict[str, Any]] = None
    alignment_config: Optional[AlignmentEvaluationConfig] = None
    filler: FillerEvaluationConfig = Field(default_factory=FillerEvaluationConfig)
    silence: SilenceEvaluationConfig = Field(default_factory=SilenceEvaluationConfig)
    speakers: SpeakerEvaluationConfig = Field(default_factory=SpeakerEvaluationConfig)
