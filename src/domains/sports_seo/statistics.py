"""Statistical analysis for the sports SEO domain.

Thin wrapper over the generic engine: the domain decides *which* features and
which outcomes are tested, the engine decides *how*.
"""

from __future__ import annotations

import polars as pl

from src.analytics.feature_registry import FeatureRegistry, FeatureRole
from src.domains.sports_seo.features import FEATURE_REGISTRY, OUTCOME_FEATURES
from src.domains.sports_seo.schema import ANALYSIS_KEY
from src.platform.config import PlatformConfig, load_platform_config
from src.statistics.engine import RESULT_COLUMNS, run_tests

CONTINUOUS_OUTCOME = ANALYSIS_KEY.outcome  # svtp_score
BINARY_OUTCOME = "page_trend"


def candidate_features(registry: FeatureRegistry | None = None) -> list:
    """Feature-side declarations: everything testable that is not an outcome."""
    reg = registry or FEATURE_REGISTRY
    excluded = set(OUTCOME_FEATURES)
    return [
        spec
        for spec in reg.testable()
        if spec.name not in excluded
        and spec.role in {FeatureRole.FEATURE, FeatureRole.COMPETITOR}
    ]


def build_evidence(
    frame: pl.DataFrame,
    registry: FeatureRegistry | None = None,
    config: PlatformConfig | None = None,
) -> pl.DataFrame:
    """Run the statistical engine over the domain feature set.

    Outcomes:

    * ``svtp_score`` - continuous visibility proxy (Spearman, Mann-Whitney);
    * ``page_trend`` - binary class derived from the SVTP ranking (Mann-Whitney,
      chi-square).

    Interpretation stays associational: no causal claim is produced here.
    """
    cfg = config or load_platform_config()
    return run_tests(
        frame,
        candidate_features(registry),
        outcome=CONTINUOUS_OUTCOME,
        binary_outcome=BINARY_OUTCOME,
        config=cfg,
    ).select(RESULT_COLUMNS)


def significant(evidence: pl.DataFrame, alpha: float | None = None) -> pl.DataFrame:
    """FDR-significant results only, ordered by effect strength."""
    if evidence.is_empty():
        return evidence
    filtered = evidence.filter(
        pl.col("status").eq("ok") & pl.col("significant_fdr") & pl.col("q_value").is_not_null()
    )
    return filtered.sort("q_value")
