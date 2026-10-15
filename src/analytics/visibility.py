"""Search Visibility Trend Proxy (SVTP).

The SVTP is an *externally observable* proxy built from rank, entity and
demand signals. It is not Search Console performance and it does not measure
clicks, impressions or absolute search volume.

Demand handling is deliberately explicit:

* when no usable Google Trends (or equivalent) observations exist, the model
  reports ``demand_component_available = false``, uses a documented neutral
  value, and never claims that temporal demand was measured;
* when a demand dataset is supplied later, ``DemandSignal`` activates without
  any change to the page-diagnostic architecture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

from src.platform.config import PlatformConfig, load_platform_config


@dataclass
class RankSignal:
    """Observed organic position signal."""

    column: str = "serp_position"
    higher_is_better: bool = False
    available: bool = True


@dataclass
class DemandSignal:
    """Temporal demand signal (Google Trends or equivalent).

    Explicitly nullable: the executed sports SEO dataset contains no usable
    Google Trends observations, so this component is neutral by default.
    """

    column: str | None = None
    available: bool = False
    neutral_value: float = 0.5

    def values(self, frame: pl.DataFrame) -> pl.Series:
        """Demand values as a float series, neutral when unavailable."""
        if not self.available or self.column is None or self.column not in frame.columns:
            return pl.Series("demand_score_norm", [self.neutral_value] * frame.height)
        return frame[self.column].cast(pl.Float64).fill_null(self.neutral_value)


@dataclass
class EntitySignal:
    """Entity-level visibility signal aggregated from its observations."""

    column: str = "entity"
    available: bool = True


@dataclass
class VisibilityResult:
    """SVTP output columns."""

    frame: pl.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


class VisibilityModel:
    """Composable visibility model: rank + entity + (optional) demand."""

    def __init__(
        self,
        config: PlatformConfig | None = None,
        rank: RankSignal | None = None,
        demand: DemandSignal | None = None,
        entity: EntitySignal | None = None,
    ) -> None:
        self.config = config or load_platform_config()
        settings = self.config.visibility
        self.rank = rank or RankSignal()
        self.entity = entity or EntitySignal()
        self.demand = demand or DemandSignal(
            available=settings.demand_component_available,
            neutral_value=settings.demand_neutral_value,
        )
        self.weights = dict(settings.weights)

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _minmax(series: pl.Series) -> pl.Series:
        """Rescale a numeric series to [0, 1]; constant series map to 0.5."""
        numeric = series.cast(pl.Float64)
        low = numeric.min()
        high = numeric.max()
        if low is None or high is None or high == low:
            return pl.Series(series.name, [0.5] * numeric.len())
        return (numeric - low) / (high - low)

    def compute(self, frame: pl.DataFrame) -> VisibilityResult:
        """Add SVTP columns to ``frame`` (never mutates the input)."""
        if self.rank.column not in frame.columns:
            raise KeyError(f"rank column {self.rank.column!r} missing from frame")

        positions = frame[self.rank.column].cast(pl.Float64)
        observed_max = positions.max() or 1.0
        observed_max = observed_max if observed_max > 0 else 1.0

        if self.rank.higher_is_better:
            rank_score = (positions - (positions.min() or 0.0)) / observed_max
        else:
            rank_score = (observed_max - positions + 1.0) / observed_max
        rank_score = rank_score.fill_null(0.0).alias("rank_score")

        # Entity signal: mean rank score of every observation of that entity.
        base = frame.with_columns(rank_score)
        if self.entity.available and self.entity.column in frame.columns:
            entity_visibility = (
                base.group_by(self.entity.column)
                .agg(pl.col("rank_score").mean().alias("entity_visibility"))
            )
            base = base.join(entity_visibility, on=self.entity.column, how="left")
            base = base.with_columns(
                pl.col("entity_visibility").fill_null(pl.col("rank_score")).alias(
                    "entity_visibility"
                )
            )
        else:
            base = base.with_columns(pl.col("rank_score").alias("entity_visibility"))

        base = base.with_columns(
            [
                self._minmax(base["rank_score"]).alias("rank_score_norm"),
                self._minmax(base["entity_visibility"]).alias("entity_score_norm"),
                self.demand.values(base).alias("demand_score_norm"),
            ]
        ).with_columns(
            pl.lit(self.demand.available).alias("demand_component_available")
        )

        weights = self.weights
        weighted = (
            weights.get("rank", 0.0) * pl.col("rank_score_norm")
            + weights.get("entity", 0.0) * pl.col("entity_score_norm")
            + (weights.get("demand", 0.0) if self.demand.available else 0.0)
            * pl.col("demand_score_norm")
        )
        total_weight = weights.get("rank", 0.0) + weights.get("entity", 0.0) + (
            weights.get("demand", 0.0) if self.demand.available else 0.0
        )
        if total_weight <= 0:
            total_weight = 1.0

        base = base.with_columns(
            (weighted / total_weight).fill_null(0.5).alias("svtp_score"),
            pl.when(pl.col(self.rank.column) <= 3)
            .then(pl.lit("1-3"))
            .when(pl.col(self.rank.column) <= 6)
            .then(pl.lit("4-6"))
            .otherwise(pl.lit("7-10"))
            .alias("rank_group"),
            pl.when(pl.col(self.rank.column) <= 5)
            .then(pl.lit("top_half"))
            .otherwise(pl.lit("bottom_half"))
            .alias("rank_group_robust"),
        )

        # Balanced label: exactly half of the observations in each class.
        # Ordinal ranking makes the split deterministic for tied scores.
        base = base.with_columns(
            pl.col("svtp_score")
            .rank("ordinal", descending=True)
            .le(pl.len() // 2)
            .cast(pl.Int64)
            .alias("page_trend")
        )

        metadata = {
            "demand_component_available": self.demand.available,
            "demand_neutral_value": self.demand.neutral_value,
            "weights": weights,
            "rank_column": self.rank.column,
            "entity_column": self.entity.column,
            "observations": base.height,
            "median_svtp": float(base["svtp_score"].median() or 0.0),
            "interpretation": (
                "externally observable SERP/entity visibility proxy; "
                "not Search Console performance and not measured temporal demand"
            ),
        }
        return VisibilityResult(frame=base, metadata=metadata)

    def describe(self) -> dict[str, Any]:
        """Human-readable model description for docs and the CLI."""
        return {
            "model": "Search Visibility Trend Proxy",
            "components": {
                "rank": {"column": self.rank.column, "available": self.rank.available},
                "entity": {"column": self.entity.column, "available": self.entity.available},
                "demand": {
                    "column": self.demand.column,
                    "available": self.demand.available,
                    "note": "neutral while no usable Google Trends observations exist",
                },
            },
            "weights": self.weights,
        }
