"""
Evaluation schema for word-level alignment accuracy testing.

Defines the data models for alignment evaluation fixtures — ground-truth
word boundaries that can be compared against the alignment pipeline output.
"""

from pydantic import BaseModel
from typing import Optional


class WordAlignmentExpectation(BaseModel):
    """Ground-truth word boundary for a single word."""
    word: str
    expected_start: float           # seconds
    expected_end: float             # seconds
    tolerance_ms: float = 66.0      # default: 2 frames at 30fps


class AlignmentEvaluationConfig(BaseModel):
    """Configuration for an alignment accuracy evaluation."""
    audio_path: str
    language: str = "en"
    word_expectations: list[WordAlignmentExpectation]
    # Global tolerance override (if set, applies to all words)
    global_tolerance_ms: Optional[float] = None
    # Targets
    target_median_error_ms: float = 15.0
    target_pct_within_1_frame: float = 85.0   # % of words within 33ms
    target_pct_within_2_frames: float = 95.0  # % of words within 66ms


class EvaluationFixture(BaseModel):
    """A complete evaluation fixture with metadata."""
    name: str
    description: str = ""
    alignment_config: Optional[AlignmentEvaluationConfig] = None
