"""Column mapping of the sports SEO dataset onto the generic model."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from src.analytics.base_model import AnalysisKey

ANALYSIS_KEY = AnalysisKey(
    entity="entity",
    document="url",
    query="query",
    outcome="svtp_score",
    position="serp_position",
    decision="primary_action",
    score="primary_action_score",
    priority="action_priority",
)

REQUIRED_COLUMNS = ["url", "query", "domain", "entity", "serp_position", "is_valid"]


@dataclass(frozen=True)
class FrameCheck:
    """Result of validating an analysis frame."""

    missing: list[str]
    rows: int

    @property
    def ok(self) -> bool:
        """True when every required column is present."""
        return not self.missing


def check_analysis_frame(frame: pl.DataFrame) -> FrameCheck:
    """Verify the frame carries everything the domain engines require."""
    return FrameCheck(
        missing=[column for column in REQUIRED_COLUMNS if column not in frame.columns],
        rows=frame.height,
    )


def valid_observations(frame: pl.DataFrame) -> pl.DataFrame:
    """Rows that passed Silver validation (invalid rows stay quarantined)."""
    if "is_valid" in frame.columns:
        return frame.filter(pl.col("is_valid"))
    return frame
