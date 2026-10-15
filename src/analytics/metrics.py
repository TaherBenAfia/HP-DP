"""Generic metric aggregation used to publish Gold entity/query/page tables."""

from __future__ import annotations

import polars as pl

from src.analytics.base_model import AnalysisKey


def _available(frame: pl.DataFrame, columns: list[str]) -> list[str]:
    return [column for column in columns if column in frame.columns]


def _aggregate(frame: pl.DataFrame, by: str, metrics: list[str]) -> pl.DataFrame:
    """Count observations and summarise numeric metrics per key."""
    aggregations: list[pl.Expr] = [pl.len().alias("observations")]
    for metric in metrics:
        aggregations.extend(
            [
                pl.col(metric).mean().alias(f"{metric}_mean"),
                pl.col(metric).median().alias(f"{metric}_median"),
                pl.col(metric).min().alias(f"{metric}_min"),
                pl.col(metric).max().alias(f"{metric}_max"),
            ]
        )
    return frame.group_by(by).agg(aggregations)


def entity_metrics(
    frame: pl.DataFrame, key: AnalysisKey, metrics: list[str] | None = None
) -> pl.DataFrame:
    """Gold ``entity_metrics``: one row per entity."""
    numeric = metrics or [key.outcome, key.score]
    return _aggregate(frame, key.entity, _available(frame, numeric)).sort(
        "observations", descending=True
    )


def query_metrics(
    frame: pl.DataFrame, key: AnalysisKey, metrics: list[str] | None = None
) -> pl.DataFrame:
    """Gold ``query_metrics``: one row per query."""
    numeric = metrics or [key.outcome, key.position, key.score]
    return _aggregate(frame, key.query, _available(frame, numeric)).sort(
        "observations", descending=True
    )


def page_metrics(
    frame: pl.DataFrame, key: AnalysisKey, metrics: list[str] | None = None
) -> pl.DataFrame:
    """Gold ``page_metrics``: one row per document."""
    numeric = metrics or [key.outcome, key.position, key.score]
    return _aggregate(frame, key.document, _available(frame, numeric)).sort(
        "observations", descending=True
    )


def action_distribution(frame: pl.DataFrame, key: AnalysisKey) -> pl.DataFrame | None:
    """Observation counts per (entity, action) pair."""
    if key.entity not in frame.columns or key.decision not in frame.columns:
        return None
    return (
        frame.group_by([key.entity, key.decision])
        .agg(pl.len().alias("observations"))
        .sort("observations", descending=True)
    )
