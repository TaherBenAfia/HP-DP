"""Transparent action engine.

Every score in this module is reproducible arithmetic on published columns:

* ``deficit(feature, row)`` - how far a row is from a documented target

# type: ignore
  (binary presence, an acceptable range, or the empirical distribution);
* ``family_score`` - the weighted mean of its feature deficits;
* ``primary_action`` - the family with the largest deficit.

Statistical evidence (FDR-adjusted effect sizes) only *re-weights* deficits;
it never introduces opaque terms. Nothing here is causal: recommendations are
diagnostics, not guarantees of ranking improvement.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from src.analytics.feature_registry import FeatureKind, FeatureRegistry, FeatureSpec
from src.platform.config import PlatformConfig, load_platform_config

DEFICIT_PREFIX = "deficit__"


@dataclass
class DeficitRule:
    """Target definition for a single feature inside an action family."""

    kind: str = "percentile"  # "binary" | "range" | "percentile"
    desired: bool | None = None
    low: float | None = None
    high: float | None = None
    note: str = ""


@dataclass
class ActionFamily:
    """One of the platform's action families."""

    name: str
    description: str
    features: list[str]
    weights: dict[str, float] = field(default_factory=dict)
    rules: dict[str, DeficitRule] = field(default_factory=dict)
    recommendations: dict[str, str] = field(default_factory=dict)
    summary: str = ""

    def weight(self, feature: str) -> float:
        """Base weight of a feature (default 1.0)."""
        return float(self.weights.get(feature, 1.0))


# -- deficit rules ---------------------------------------------------------
def _binary_deficit(series: pl.Series, rule: DeficitRule) -> pl.Series:
    desired = bool(rule.desired) if rule.desired is not None else True
    values = series.cast(pl.Boolean, strict=False).fill_null(not desired)
    return (values != desired).cast(pl.Float64)


def _range_deficit(series: pl.Series, rule: DeficitRule) -> pl.Series:
    values = series.cast(pl.Float64, strict=False)
    low = float(rule.low if rule.low is not None else 0.0)
    high = float(rule.high if rule.high is not None else low)
    deficit = pl.Series(values.name, [0.0] * values.len(), dtype=pl.Float64)
    if low > 0:
        below = ((low - values) / low).clip(0.0, 1.0).fill_null(0.0)
        deficit = deficit + pl.when(values < low).then(below).otherwise(0.0)
    if high > low:
        above = ((values - high) / high).clip(0.0, 1.0).fill_null(0.0)
        deficit = deficit + pl.when(values > high).then(above).otherwise(0.0)
    return deficit.cast(pl.Float64).fill_null(0.0)


def _percentile_deficit(series: pl.Series, higher_is_better: bool) -> pl.Series:
    values = series.cast(pl.Float64, strict=False)
    non_null = values.drop_nulls().len() or 1
    pct = (values.rank("average") - 0.5) / non_null
    deficit = (1.0 - pct) if higher_is_better else pct
    return deficit.cast(pl.Float64)


def _deficit(spec: FeatureSpec, series: pl.Series, rule: DeficitRule) -> pl.Series:
    if rule.kind == "binary" or (rule.kind == "percentile" and spec.kind == FeatureKind.BINARY):
        return _binary_deficit(series, rule)
    if rule.kind == "range":
        return _range_deficit(series, rule)
    higher = spec.higher_is_better if spec.higher_is_better is not None else True
    return _percentile_deficit(series, higher)


def compute_deficits(
    frame: pl.DataFrame,
    registry: FeatureRegistry,
    families: Iterable[ActionFamily],
) -> pl.DataFrame:
    """Add one ``deficit__<feature>`` column per requested feature.

    Constant (zero-variance) features are skipped: a column that never varies
    cannot discriminate between rows and must not dilute a family score.
    """
    out = frame
    for family in families:
        for name in family.features:
            column = f"{DEFICIT_PREFIX}{name}"
            if name not in out.columns or column in out.columns:
                continue
            if out[name].n_unique() < 2:
                continue
            spec = registry.get(name) or FeatureSpec(name=name, family=family.name)
            rule = family.rules.get(name, DeficitRule())
            out = out.with_columns(_deficit(spec, out[name], rule).alias(column))
    return out


# -- evidence --------------------------------------------------------------
def evidence_support(evidence: pl.DataFrame | None) -> dict[str, dict[str, float]]:
    """feature -> statistics summary used to re-weight deficits.

    Only FDR-significant results contribute; ``support`` is
    ``min(|effect_size| / 0.5, 1)``.
    """
    if evidence is None or evidence.is_empty() or "feature" not in evidence.columns:
        return {}
    supported: dict[str, dict[str, float]] = {}
    rows = evidence.filter(
        pl.col("status").eq("ok")
        & pl.col("significant_fdr")
        & pl.col("effect_size").is_not_null()
    )
    for row in rows.iter_rows(named=True):
        effect = abs(float(row["effect_size"] or 0.0))
        feature = str(row["feature"])
        if feature not in supported or effect > supported[feature]["abs_effect"]:
            supported[feature] = {
                "effect": float(row["effect_size"] or 0.0),
                "abs_effect": effect,
                "support": min(effect / 0.5, 1.0),
                "q_value": float(row["q_value"]) if row["q_value"] is not None else 1.0,
                "test": str(row["test"]),
            }
    return supported


def _effective_weight(base: float, support: dict[str, float] | None) -> float:
    return base * (0.5 + 0.5 * float(support["support"])) if support else base * 0.5


# -- scoring ---------------------------------------------------------------
def score_actions(
    frame: pl.DataFrame,
    families: list[ActionFamily],
    registry: FeatureRegistry,
    evidence: pl.DataFrame | None = None,
    config: PlatformConfig | None = None,
) -> pl.DataFrame:
    """Score every row against every action family.

    Effective weight of a feature::

        w_eff = w * (0.5 + 0.5 * support)

    Unsupported diagnostics therefore keep half weight: their deficit is
    still real, but statistical evidence can only strengthen a feature, never
    invent one.
    """
    cfg = config or load_platform_config()
    thresholds = cfg.action_engine.priority_thresholds
    high = float(thresholds.get("high", 0.66))
    medium = float(thresholds.get("medium", 0.33))
    support = evidence_support(evidence)

    scored = compute_deficits(frame, registry, families)
    family_names = [family.name for family in families]

    # Per-row deficit/weight structures, evaluated once.
    family_rows: list[dict[str, tuple[str, float, float, dict[str, float]]]] = []
    family_rows = [{} for _ in range(frame.height)]
    scores: dict[str, list[float]] = {}

    for family in families:
        contributions: list[tuple[str, float, float, dict[str, float]]] = []
        for name in family.features:
            column = f"{DEFICIT_PREFIX}{name}"
            if column not in scored.columns:
                continue
            feature_support = support.get(name, {})
            w_eff = _effective_weight(family.weight(name), feature_support)
            contributions.append((name, w_eff, column, feature_support))

        values: list[float] = []
        for index in range(frame.height):
            numerator = 0.0
            denominator = 0.0
            entries: dict[str, tuple[str, float, float, dict[str, float]]] = {}
            for name, w_eff, column, feature_support in contributions:
                value = scored[column][index]
                if value is None:
                    continue
                deficit = float(value)
                numerator += deficit * w_eff
                denominator += w_eff
                entries[name] = (name, w_eff, deficit, feature_support)
            score = numerator / denominator if denominator > 0 else 0.0
            values.append(score)
            if denominator > 0:
                family_rows[index].update(entries)
        scores[f"{family.name}_action_score"] = values

    primary_action: list[str] = []
    secondary_action: list[str] = []
    primary_score: list[float] = []
    evidence_json: list[str] = []
    reasons: list[str] = []
    recommendations: list[str] = []

    by_name = {family.name: family for family in families}

    for index in range(frame.height):
        ranked = sorted(
            ((name, scores[f"{name}_action_score"][index]) for name in family_names),
            key=lambda item: item[1],
            reverse=True,
        )
        top_name, top_score = ranked[0]
        primary_action.append(top_name)
        secondary_action.append(ranked[1][0] if len(ranked) > 1 else "")
        primary_score.append(top_score)

        family = by_name[top_name]
        entries = sorted(
            family_rows[index].values(),
            key=lambda entry: entry[2] * entry[1],
            reverse=True,
        )[:3]
        payload = [
            {
                "feature": name,
                "deficit": round(deficit, 4),
                "weight": round(weight, 4),
                "support": round(entry.get("support", 0.0), 4),
                "q_value": round(entry.get("q_value", 1.0), 6),
            }
            for name, weight, deficit, entry in entries
        ]
        evidence_json.append(json.dumps(payload, sort_keys=True))

        contributors = ", ".join(f"{item['feature']}={item['deficit']:.2f}" for item in payload)
        reasons.append(
            f"{top_name} has the largest deficit (score {top_score:.2f}) "
            f"driven by {contributors or 'no available feature'}. "
            "Observational diagnostics only; association is not causation."
        )
        top_feature = payload[0]["feature"] if payload else ""
        recommendations.append(
            family.recommendations.get(top_feature) or family.summary
        )

    result = scored.with_columns(
        [
            pl.Series(name, values, dtype=pl.Float64)
            for name, values in scores.items()
        ]
        + [
            pl.Series("primary_action", primary_action),
            pl.Series("secondary_action", secondary_action),
            pl.Series("primary_action_score", primary_score, dtype=pl.Float64),
            pl.Series("evidence_features", evidence_json),
            pl.Series("reason", reasons),
            pl.Series("recommended_action", recommendations),
        ]
    ).with_columns(
        pl.when(pl.col("primary_action_score") >= high)
        .then(pl.lit("high"))
        .when(pl.col("primary_action_score") >= medium)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("low"))
        .alias("action_priority")
    )

    score_columns = [
        pl.col(f"{name}_action_score").cast(pl.Float64) for name in family_names
    ]
    identity = [
        column
        for column in ("url", "domain", "entity", "query", "serp_position")
        if column in result.columns
    ]
    return result.select(
        identity
        + [
            "primary_action",
            "secondary_action",
            "primary_action_score",
            "action_priority",
            "evidence_features",
            "recommended_action",
            "reason",
        ]
        + [expr.meta.output_name() for expr in score_columns]
    ).with_columns(score_columns)


def family_report(families: list[ActionFamily]) -> list[dict[str, Any]]:
    """Describe the action families for documentation and the CLI."""
    return [
        {
            "family": family.name,
            "description": family.description,
            "features": family.features,
            "summary": family.summary,
        }
        for family in families
    ]
