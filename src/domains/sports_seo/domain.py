"""Sports SEO domain adapter."""

from __future__ import annotations

from typing import Any

import polars as pl

from src.analytics.action_engine import score_actions
from src.analytics.base_model import AnalysisKey
from src.analytics.feature_registry import FeatureRegistry
from src.analytics.visibility import VisibilityModel
from src.domains.base import DomainAdapter
from src.domains.sports_seo.actions import FAMILIES
from src.domains.sports_seo.features import FEATURE_REGISTRY
from src.domains.sports_seo.metrics import build_metrics
from src.domains.sports_seo.schema import ANALYSIS_KEY, check_analysis_frame
from src.domains.sports_seo.statistics import build_evidence


class SportsSEODomain(DomainAdapter):
    """First implementation of the generic domain interface."""

    domain = "sports_seo"
    domain_version = "1.0.0"

    @property
    def key(self) -> AnalysisKey:
        """Column mapping onto the generic analytical model."""
        return ANALYSIS_KEY

    @property
    def features(self) -> FeatureRegistry:
        """Declared domain feature set."""
        return FEATURE_REGISTRY

    def build_features(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Add the visibility model columns (SVTP, trend label, rank bands)."""
        check = check_analysis_frame(frame)
        if not check.ok:
            raise ValueError(f"analysis frame is missing columns: {check.missing}")
        result = VisibilityModel(config=self.config).compute(frame)
        return result.frame

    def build_metrics(self, frame: pl.DataFrame) -> dict[str, pl.DataFrame]:
        """Gold metric tables for this domain."""
        return build_metrics(frame)

    def run_statistics(self, frame: pl.DataFrame) -> pl.DataFrame:
        """FDR-adjusted statistical evidence over the domain features."""
        return build_evidence(frame, FEATURE_REGISTRY, self.config)

    def build_actions(
        self, frame: pl.DataFrame, evidence: pl.DataFrame | None = None
    ) -> pl.DataFrame:
        """Score the six action families with statistical re-weighting."""
        return score_actions(
            frame,
            FAMILIES,
            FEATURE_REGISTRY,
            evidence=evidence,
            config=self.config,
        )

    def describe(self) -> dict[str, Any]:
        """Domain description for the CLI and the dashboard."""
        base = super().describe()
        base["action_families"] = [family.name for family in FAMILIES]
        base["visibility_model"] = VisibilityModel(config=self.config).describe()
        return base


ADAPTER = SportsSEODomain
