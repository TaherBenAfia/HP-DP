"""Generic analytical base model.

The platform models every domain with the same conceptual entities:

    ENTITY | DOCUMENT/PAGE | QUERY | OBSERVATION | FEATURE |
    OUTCOME/VISIBILITY | COMPETITOR | ACTION

A domain adapter maps its columns onto this model; the engines below never
learn domain-specific column names.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from src.analytics.feature_registry import FeatureRegistry, FeatureRole


@dataclass(frozen=True)
class AnalysisKey:
    """Column mapping from a domain frame onto the generic model."""

    entity: str = "entity"
    document: str = "url"
    query: str = "query"
    outcome: str = "svtp_score"
    position: str = "serp_position"
    decision: str = "primary_action"
    score: str = "primary_action_score"
    priority: str = "action_priority"


@dataclass
class ModelValidation:
    """Result of checking that a frame can be mapped to the generic model."""

    missing_columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every required column is present."""
        return not self.missing_columns


class AnalyticalBaseModel:
    """Read-only view of a Silver/analytical frame in generic terms."""

    def __init__(
        self,
        frame: pl.DataFrame,
        key: AnalysisKey | None = None,
        registry: FeatureRegistry | None = None,
    ) -> None:
        self.frame = frame
        self.key = key or AnalysisKey()
        self.registry = registry or FeatureRegistry()

    # -- validation --------------------------------------------------------
    def validate(self) -> ModelValidation:
        """Check the mapping required by the generic model."""
        missing = [
            column
            for column in (self.key.entity, self.key.document, self.key.query)
            if column not in self.frame.columns
        ]
        warnings: list[str] = []
        if self.key.position not in self.frame.columns:
            warnings.append(f"outcome position column {self.key.position!r} is absent")
        if not self.frame.height:
            warnings.append("frame contains no observations")
        return ModelValidation(missing_columns=missing, warnings=warnings)

    # -- generic tables ----------------------------------------------------
    @property
    def observation_id(self) -> pl.Series:
        """Stable identifier for an observation (document x query)."""
        key = pl.struct([pl.col(self.key.document), pl.col(self.key.query)])
        return key.hash().map_elements(lambda value: f"obs_{int(value):016x}", return_dtype=pl.String)

    def observations(self) -> pl.DataFrame:
        """Observations with a stable id and the generic role columns."""
        columns = [
            column
            for column in (
                self.key.entity,
                self.key.document,
                self.key.query,
                self.key.position,
                self.key.outcome,
                self.key.decision,
                self.key.score,
                self.key.priority,
            )
            if column in self.frame.columns
        ]
        return self.frame.select(columns).with_columns(self.observation_id.alias("observation_id"))

    def entities(self) -> pl.DataFrame:
        """Distinct entities with observation counts."""
        return (
            self.frame.group_by(self.key.entity)
            .agg(pl.len().alias("observations"), pl.col(self.key.document).n_unique().alias("documents"))
            .sort("observations", descending=True)
        )

    def documents(self) -> pl.DataFrame:
        """Distinct documents with observation counts."""
        return (
            self.frame.group_by(self.key.document)
            .agg(pl.len().alias("observations"), pl.col(self.key.query).n_unique().alias("queries"))
            .sort("observations", descending=True)
        )

    def queries(self) -> pl.DataFrame:
        """Distinct queries with observation counts."""
        return (
            self.frame.group_by(self.key.query)
            .agg(pl.len().alias("observations"), pl.col(self.key.document).n_unique().alias("documents"))
            .sort("observations", descending=True)
        )

    def feature_columns(self) -> list[str]:
        """Registered feature names that exist in the frame."""
        return [
            spec.name
            for spec in self.registry.testable()
            if spec.name in self.frame.columns
        ]

    def outcome_series(self) -> pl.Series | None:
        """The outcome/visibility series, if available."""
        for candidate in (self.key.outcome, self.key.position):
            if candidate in self.frame.columns:
                return self.frame[candidate]
        return None

    def identity_columns(self) -> list[str]:
        """Columns carrying the generic identity roles."""
        return [
            spec.name
            for spec in self.registry.roles(FeatureRole.IDENTITY)
            if spec.name in self.frame.columns
        ]
