"""Analytical and statistical method registry.

Every analytical method declares its purpose, required input columns, output schema,
assumptions, missing-data policy, and interpretation limitations.
The planner performs transparent capability matching against available data attributes.
"""

from __future__ import annotations

from typing import Any, Callable
import polars as pl
from pydantic import BaseModel, Field

from src.analytics.feature_registry import FeatureKind, FeatureSpec
from src.statistics.engine import run_tests


class AnalyticalMethod(BaseModel):
    """Specification of a reusable analytical or statistical computation."""

    method_id: str = Field(..., description="Unique stable method identifier")
    version: str = Field("1.0.0", description="Method implementation version")
    name: str = Field(..., description="Human-readable title")
    purpose: str = Field(..., description="Analytical problem solved")
    evaluation_objective: str = Field(..., description="e.g. 'baseline_summary', 'associational_evidence'")
    required_columns: list[str] = Field(default_factory=list, description="Must be present in input dataframe")
    min_observations: int = Field(30, description="Minimum sample size required by statistical assumptions")
    output_schema: str = Field(..., description="Schema contract of output")
    assumptions: list[str] = Field(default_factory=list, description="Statistical assumptions (e.g. non-parametric, monotonic)")
    missing_data_policy: str = Field("drop_nulls", description="Strategy for null values")
    is_deterministic: bool = Field(True, description="Whether output is deterministic")
    interpretation_limits: str = Field(
        "Associational only; does not establish causal direction.",
        description="Explicit limitation disclaimer",
    )

    execute_fn: Any = Field(default=None, exclude=True)


class MethodRegistry:
    """Registry maintaining available analytical methods and capability matching."""

    def __init__(self) -> None:
        self._methods: dict[str, AnalyticalMethod] = {}

    def register(self, method: AnalyticalMethod) -> None:
        self._methods[method.method_id] = method

    def get(self, method_id: str) -> AnalyticalMethod:
        if method_id not in self._methods:
            raise KeyError(f"Method '{method_id}' is not registered")
        return self._methods[method_id]

    def list_methods(self) -> list[AnalyticalMethod]:
        return list(self._methods.values())

    def match(
        self,
        available_columns: set[str],
        row_count: int,
        objective: str | None = None,
    ) -> list[AnalyticalMethod]:
        """Find methods whose column, sample-size, and objective preconditions are satisfied."""
        matched = []
        for method in self._methods.values():
            if objective and method.evaluation_objective != objective:
                continue
            if row_count < method.min_observations:
                continue
            if not set(method.required_columns).issubset(available_columns):
                continue
            matched.append(method)
        return matched


# -----------------------------------------------------------------------------
# Concrete Method Implementations
# -----------------------------------------------------------------------------

def run_descriptive_summary(df: pl.DataFrame, params: dict[str, Any] | None = None) -> pl.DataFrame:
    """Method A: Descriptive summary statistics across numeric observation columns."""
    numeric_cols = [col for col, dtype in df.schema.items() if dtype.is_numeric() and not col.startswith("_")]
    if not numeric_cols:
        return pl.DataFrame()

    summaries = []
    for col in numeric_cols:
        s = df[col].drop_nulls()
        if s.len() == 0:
            continue
        summaries.append(
            {
                "feature": col,
                "n_observations": s.len(),
                "mean": float(s.mean() or 0.0),
                "std": float(s.std() or 0.0) if s.len() > 1 else 0.0,
                "median": float(s.median() or 0.0),
                "min": float(s.min() or 0.0),
                "max": float(s.max() or 0.0),
            }
        )
    return pl.DataFrame(summaries)


def run_statistical_evidence(df: pl.DataFrame, params: dict[str, Any] | None = None) -> pl.DataFrame:
    """Method B: Associational statistical evidence using existing StatisticsEngine (Spearman + FDR)."""
    outcome = (params or {}).get("outcome", "rank")
    binary_outcome = (params or {}).get("binary_outcome", "top_10")
    if binary_outcome not in df.columns:
        df = df.with_columns((pl.col(outcome) <= 10).cast(pl.Int64).alias(binary_outcome))

    features = [
        FeatureSpec(name=col, family="features", kind=FeatureKind.NUMERIC)
        for col, dtype in df.schema.items()
        if dtype.is_numeric() and col not in {outcome, binary_outcome} and not col.startswith("_")
    ]
    if not features:
        return pl.DataFrame()

    return run_tests(
        frame=df,
        features=features,
        outcome=outcome,
        binary_outcome=binary_outcome,
    )


def create_default_method_registry() -> MethodRegistry:
    """Initialize registry with baseline analytical methods."""
    registry = MethodRegistry()

    # Method 1: Descriptive baseline
    registry.register(
        AnalyticalMethod(
            method_id="descriptive_statistics_v1",
            version="1.0.0",
            name="Descriptive Summary Aggregation",
            purpose="Compute mean, median, standard deviation, and range for numeric features",
            evaluation_objective="baseline_summary",
            required_columns=["rank"],
            min_observations=5,
            output_schema="descriptive_summary:1.0.0",
            assumptions=["Independent observations"],
            missing_data_policy="drop_nulls",
            is_deterministic=True,
            interpretation_limits="Descriptive only; summarizes central tendency and dispersion.",
            execute_fn=run_descriptive_summary,
        )
    )

    # Method 2: Statistical evidence testing
    registry.register(
        AnalyticalMethod(
            method_id="associational_evidence_fdr_v1",
            version="1.0.0",
            name="Spearman Correlation with Benjamini-Hochberg FDR",
            purpose="Evaluate monotonic associations with search rankings while controlling False Discovery Rate",
            evaluation_objective="associational_evidence",
            required_columns=["rank", "page_speed_score"],
            min_observations=10,
            output_schema="statistical_evidence:1.0.0",
            assumptions=["Monotonic relationship", "Continuous or ordinal observations", "Independent pairs"],
            missing_data_policy="drop_nulls_pairwise",
            is_deterministic=True,
            interpretation_limits="Observational correlation only; strictly does not imply causation.",
            execute_fn=run_statistical_evidence,
        )
    )

    return registry
