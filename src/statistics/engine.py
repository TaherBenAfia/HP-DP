"""Statistical evidence engine.

Tests implemented:

* Spearman rank correlation between a numeric feature and the outcome;
* Mann-Whitney U comparing the two outcome classes;
* Chi-square of independence for binary/categorical features;
* Benjamini-Hochberg FDR correction applied *within each test family*;
* effect sizes reported alongside every statistic.

Interpretation rules enforced by the output schema:

* results are *associations*, never causal effects;
* a p-value is not the probability that a hypothesis is true;
* ``q_value`` is the FDR-adjusted significance measure used for selection.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import polars as pl
from scipy import stats as scipy_stats

from src.analytics.feature_registry import FeatureKind, FeatureSpec
from src.platform.config import PlatformConfig, load_platform_config

RESULT_COLUMNS = [
    "feature",
    "family",
    "outcome",
    "test",
    "n",
    "statistic",
    "p_value",
    "effect_size",
    "effect_size_type",
    "q_value",
    "significant_fdr",
    "direction",
    "status",
    "notes",
]


@dataclass
class StatResult:
    """One statistical test outcome."""

    feature: str
    family: str
    outcome: str
    test: str
    n: int
    statistic: float | None = None
    p_value: float | None = None
    effect_size: float | None = None
    effect_size_type: str = ""
    q_value: float | None = None
    significant_fdr: bool = False
    direction: str = "undefined"
    status: str = "ok"
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Flat dictionary ready for a Delta table."""
        return {
            "feature": self.feature,
            "family": self.family,
            "outcome": self.outcome,
            "test": self.test,
            "n": self.n,
            "statistic": self.statistic,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "effect_size_type": self.effect_size_type,
            "q_value": self.q_value,
            "significant_fdr": self.significant_fdr,
            "direction": self.direction,
            "status": self.status,
            "notes": self.notes,
        }


@dataclass
class HypothesisFamily:
    """A set of p-values corrected together."""

    test: str
    results: list[StatResult] = field(default_factory=list)


def benjamini_hochberg(p_values: Iterable[float], alpha: float = 0.05) -> tuple[list[float], list[bool]]:
    """Benjamini-Hochberg FDR adjustment.

    Returns ``(q_values, rejected)`` using the standard step-up procedure.
    """
    from statsmodels.stats.multitest import multipletests

    values = [float(value) for value in p_values]
    if not values:
        return [], []
    reject, adjusted, _, _ = multipletests(values, alpha=alpha, method="fdr_bh")
    return [float(value) for value in adjusted], [bool(value) for value in reject]


def _direction(effect: float | None, threshold: float = 1e-9) -> str:
    if effect is None or abs(effect) < threshold:
        return "none"
    return "positive" if effect > 0 else "negative"


def _as_numpy(series: pl.Series) -> np.ndarray:
    return series.cast(pl.Float64, strict=False).to_numpy().astype(float)


def _drop_missing(feature: pl.Series, outcome: pl.Series) -> tuple[np.ndarray, np.ndarray]:
    frame = pl.DataFrame({"x": feature, "y": outcome}).drop_nulls()
    return _as_numpy(frame["x"]), _as_numpy(frame["y"])


def spearman_test(
    feature: pl.Series, outcome: pl.Series, feature_name: str, family: str, outcome_name: str
) -> StatResult:
    """Spearman rank correlation with a continuous outcome."""
    x, y = _drop_missing(feature, outcome)
    n = len(x)
    if n < 3:
        return StatResult(feature_name, family, outcome_name, "spearman", n, status="skipped",
                          notes="insufficient observations")
    if np.unique(x).size < 2 or np.unique(y).size < 2:
        return StatResult(feature_name, family, outcome_name, "spearman", n, status="skipped",
                          notes="zero variance in feature or outcome")
    with np.errstate(invalid="ignore", divide="ignore"):
        rho, p_value = scipy_stats.spearmanr(x, y)
    if np.isnan(rho):
        return StatResult(feature_name, family, outcome_name, "spearman", n, status="skipped",
                          notes="undefined correlation")
    rho_float = float(rho)
    p_value_float = float(p_value)
    return StatResult(
        feature=feature_name,
        family=family,
        outcome=outcome_name,
        test="spearman",
        n=n,
        statistic=rho_float,
        p_value=p_value_float,
        effect_size=rho_float,
        effect_size_type="spearman_rho",
        direction=_direction(rho_float),
        notes="rank association; not a causal effect",
    )


def mann_whitney_test(
    feature: pl.Series,
    binary_outcome: pl.Series,
    feature_name: str,
    family: str,
    outcome_name: str,
) -> StatResult:
    """Mann-Whitney U comparing the two outcome classes."""
    frame = pl.DataFrame({"x": feature, "g": binary_outcome}).drop_nulls()
    group_one = _as_numpy(frame.filter(pl.col("g") == 1)["x"])
    group_zero = _as_numpy(frame.filter(pl.col("g") == 0)["x"])
    n = frame.height
    if len(group_one) < 3 or len(group_zero) < 3:
        return StatResult(feature_name, family, outcome_name, "mann_whitney_u", n,
                          status="skipped", notes="class sizes below 3")
    if np.unique(frame["x"].to_numpy()).size < 2:
        return StatResult(feature_name, family, outcome_name, "mann_whitney_u", n,
                          status="skipped", notes="zero variance in feature")
    try:
        statistic, p_value = scipy_stats.mannwhitneyu(
            group_one, group_zero, alternative="two-sided"
        )
    except ValueError as exc:
        return StatResult(feature_name, family, outcome_name, "mann_whitney_u", n,
                          status="skipped", notes=str(exc))
    n_one, n_zero = len(group_one), len(group_zero)
    effect = (2.0 * float(statistic)) / (n_one * n_zero) - 1.0
    return StatResult(
        feature=feature_name,
        family=family,
        outcome=outcome_name,
        test="mann_whitney_u",
        n=n,
        statistic=float(statistic),
        p_value=float(p_value),
        effect_size=effect,
        effect_size_type="rank_biserial",
        direction=_direction(effect),
        notes="positive effect means higher values in outcome class 1",
    )


def chi_square_test(
    feature: pl.Series,
    binary_outcome: pl.Series,
    feature_name: str,
    family: str,
    outcome_name: str,
) -> StatResult:
    """Chi-square test of independence for a categorical feature."""
    frame = pl.DataFrame({"x": feature.cast(pl.String), "g": binary_outcome}).drop_nulls()
    n = frame.height
    if n < 10:
        return StatResult(feature_name, family, outcome_name, "chi_square", n,
                          status="skipped", notes="insufficient observations")
    contingency = (
        frame.group_by(["g", "x"])
        .len()
        .pivot(on="x", index="g", values="len")
        .fill_null(0)
        .sort("g")
    )
    group_labels = contingency["g"].to_list()
    table = contingency.drop("g").to_numpy()
    if table.shape[0] < 2 or table.shape[1] < 2:
        return StatResult(feature_name, family, outcome_name, "chi_square", n,
                          status="skipped", notes="degenerate contingency table")
    try:
        chi2, p_value, dof, _ = scipy_stats.chi2_contingency(table)
    except ValueError as exc:
        return StatResult(feature_name, family, outcome_name, "chi_square", n,
                          status="skipped", notes=str(exc))
    rows, cols = table.shape
    denominator = n * (min(rows, cols) - 1)
    effect = float(np.sqrt(chi2 / denominator)) if denominator else 0.0

    levels = [column for column in contingency.columns if column != "g"]
    row_shares = table / table.sum(axis=1, keepdims=True)
    direction = "none"
    if table.shape[0] >= 2 and levels:
        reference = None
        for candidate in ("1", "true", "True"):
            if candidate in levels:
                reference = levels.index(candidate)
                break
        if reference is None:
            reference = int(np.argmax(table.sum(axis=0)))
        try:
            index_one = group_labels.index(1)
            index_zero = group_labels.index(0)
        except ValueError:
            index_one, index_zero = 1, 0
        share_one = float(row_shares[index_one, reference])
        share_zero = float(row_shares[index_zero, reference])
        if abs(share_one - share_zero) > 1e-12:
            direction = "positive" if share_one > share_zero else "negative"
    return StatResult(
        feature=feature_name,
        family=family,
        outcome=outcome_name,
        test="chi_square",
        n=n,
        statistic=float(chi2),
        p_value=float(p_value),
        effect_size=effect,
        effect_size_type="cramers_v",
        direction=direction,
        notes=f"chi-square association, dof={dof}; not a causal effect",
    )


def run_tests(
    frame: pl.DataFrame,
    features: list[FeatureSpec],
    outcome: str,
    binary_outcome: str,
    config: PlatformConfig | None = None,
) -> pl.DataFrame:
    """Run the appropriate test for every feature and adjust FDR per family.

    Features with zero variance, missing columns or too few observations are
    returned with ``status='skipped'`` so that nothing disappears silently.
    """
    cfg = config or load_platform_config()
    alpha = cfg.statistics.fdr_alpha
    min_n = cfg.statistics.min_observations

    families: dict[str, HypothesisFamily] = {}
    results: list[StatResult] = []

    for spec in features:
        if spec.name not in frame.columns:
            results.append(
                StatResult(spec.name, spec.family, outcome, "none", 0, status="skipped",
                           notes="column not present in dataset")
            )
            continue
        series = frame[spec.name]
        if series.null_count() == series.len():
            results.append(
                StatResult(spec.name, spec.family, outcome, "none", 0, status="skipped",
                           notes="column entirely null")
            )
            continue
        if series.n_unique() < 2:
            results.append(
                StatResult(spec.name, spec.family, outcome, "none", series.len(),
                           status="skipped", notes="zero variance (constant column)")
            )
            continue
        if frame.height < min_n:
            results.append(
                StatResult(spec.name, spec.family, outcome, "none", frame.height,
                           status="skipped", notes=f"fewer than {min_n} observations")
            )
            continue

        if spec.kind in {FeatureKind.BINARY, FeatureKind.CATEGORICAL}:
            test = chi_square_test(series, frame[binary_outcome], spec.name, spec.family,
                                   binary_outcome)
            families.setdefault("chi_square", HypothesisFamily("chi_square")).results.append(test)
            results.append(test)
        else:
            rank_test = spearman_test(series, frame[outcome], spec.name, spec.family, outcome)
            families.setdefault("spearman", HypothesisFamily("spearman")).results.append(rank_test)
            results.append(rank_test)
            rank_test.effect_size_type = rank_test.effect_size_type or "spearman_rho"

            if binary_outcome in frame.columns:
                class_test = mann_whitney_test(series, frame[binary_outcome], spec.name,
                                               spec.family, binary_outcome)
                families.setdefault(
                    "mann_whitney_u", HypothesisFamily("mann_whitney_u")
                ).results.append(class_test)
                results.append(class_test)

    for family in families.values():
        tested = [result for result in family.results if result.p_value is not None]
        if not tested:
            continue
        q_values, rejected = benjamini_hochberg(
            [float(result.p_value) for result in tested], alpha=alpha
        )
        for result, q_value, reject in zip(tested, q_values, rejected, strict=True):
            result.q_value = q_value
            result.significant_fdr = bool(reject)

    schema = {column: _result_schema(column) for column in RESULT_COLUMNS}
    if results:
        frame_out = pl.from_dicts([result.as_dict() for result in results], schema=schema)
    else:
        frame_out = pl.DataFrame(schema=schema)
    return frame_out.select(RESULT_COLUMNS)


def _result_schema(column: str) -> pl.DataType:
    """Stable schema for the statistical evidence table."""
    if column in {"n"}:
        return cast(pl.DataType, pl.Int64)
    if column in {"statistic", "p_value", "effect_size", "q_value"}:
        return cast(pl.DataType, pl.Float64)
    if column == "significant_fdr":
        return cast(pl.DataType, pl.Boolean)
    return cast(pl.DataType, pl.String)
