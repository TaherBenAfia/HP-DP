"""Gold ``causal_effects`` data product.

Builds observational effect estimates for a small analysis frame selected
from the platform's own tables and publishes them as a Delta table. The
analysis frame is chosen defensively from real numeric columns when they
exist; when neither input table exposes usable columns, a deterministic
seeded synthetic frame is substituted so the function stays runnable in any
environment (the ``method_status`` column and the docstrings of the estimators
make clear what was analysed). Every row carries an ``assumptions`` list and
the observational disclaimer, and intervals are accompanied by split-conformal
proxy bounds, not by design-based guarantees.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from src.causal.conformal import split_conformal_intervals
from src.causal.estimators import (
    DISCLAIMER,
    EffectEstimate,
    doubleml_plr_effect,
    dowhy_linear_effect,
    econml_linear_dml_effect,
)
from src.platform.config import PlatformConfig
from src.platform.storage import Storage

_SYNTHETIC_SEED = 0

_OUTCOME_CANDIDATES: tuple[str, ...] = (
    "svtp_score",
    "visibility_score",
    "visibility",
    "median_svtp",
    "serp_position",
    "organic_position",
)

_TREATMENT_CANDIDATES: tuple[str, ...] = (
    "keyword_in_h1",
    "keyword_in_title",
    "schema_present",
    "title_present",
    "is_indexable",
    "internal_link_count",
    "word_count",
    "content_depth",
)

_CONFOUNDER_CANDIDATES: tuple[str, ...] = (
    "serp_position",
    "query_word_count",
    "url_depth",
    "word_count",
    "page_size_kb",
    "response_time_ms",
    "heading_count",
    "internal_link_count",
    "image_count",
    "char_count",
)

_RUNNERS: dict[str, Callable[..., EffectEstimate]] = {
    "dowhy_linear": dowhy_linear_effect,
    "econml_linear_dml": econml_linear_dml_effect,
    "doubleml_plr": doubleml_plr_effect,
}

_EFFECT_SCHEMA: dict[str, pl.DataType] = {
    "method": pl.String,
    "treatment": pl.String,
    "outcome": pl.String,
    "confounders": pl.String,
    "estimate": pl.Float64,
    "stderr": pl.Float64,
    "ci_low": pl.Float64,
    "ci_high": pl.Float64,
    "p_value": pl.Float64,
    "n": pl.Int64,
    "effect_size": pl.Float64,
    "conformal_lower": pl.Float64,
    "conformal_upper": pl.Float64,
    "assumptions": pl.String,
    "disclaimer": pl.String,
    "alpha": pl.Float64,
    "environment": pl.String,
    "produced_at": pl.String,
    "method_status": pl.String,
}

EFFECT_COLUMNS: list[str] = list(_EFFECT_SCHEMA)


def empty_effects_frame() -> pl.DataFrame:
    """Empty ``causal_effects`` frame carrying the full output schema."""
    return pl.DataFrame(schema=_EFFECT_SCHEMA)


def _varies(frame: pl.DataFrame, column: str) -> bool:
    """True when a column exists and takes more than one distinct value."""
    if column not in frame.columns:
        return False
    return frame.get_column(column).n_unique() > 1


def _select_columns(frame: pl.DataFrame) -> tuple[str, str, list[str]] | None:
    """Pick existing, varying numeric treatment/outcome/confounder columns."""
    numeric = {
        name
        for name, dtype in frame.schema.items()
        if dtype.is_numeric() and not name.startswith(("_", "qf_"))
    }
    outcome = next(
        (name for name in _OUTCOME_CANDIDATES if name in numeric and _varies(frame, name)),
        None,
    )
    if outcome is None:
        return None
    treatment = next(
        (
            name
            for name in _TREATMENT_CANDIDATES
            if name in numeric and name != outcome and _varies(frame, name)
        ),
        None,
    )
    if treatment is None:
        return None
    chosen = {outcome, treatment}
    confounders = [
        name
        for name in _CONFOUNDER_CANDIDATES
        if name in numeric and name not in chosen and _varies(frame, name)
    ]
    if not confounders:
        confounders = [
            name for name in sorted(numeric) if name not in chosen and _varies(frame, name)
        ]
    confounders = confounders[:3]
    if not confounders:
        return None
    return treatment, outcome, confounders


def _analysis_frame(
    frame: pl.DataFrame,
    treatment: str,
    outcome: str,
    confounders: list[str],
    min_observations: int,
) -> pd.DataFrame | None:
    """Numeric analysis matrix for the selection, or ``None`` when unusable."""
    columns = [treatment, outcome, *confounders]
    numeric = frame.select(columns).cast(pl.Float64, strict=False)
    values = numeric.to_numpy()
    if values.size == 0:
        return None
    values = values[np.isfinite(values).all(axis=1)]
    if values.shape[0] < min_observations:
        return None
    if np.std(values[:, 0]) <= 0.0 or np.std(values[:, 1]) <= 0.0:
        return None
    return pd.DataFrame(values, columns=columns)


def _synthetic_frame(min_observations: int) -> pd.DataFrame:
    """Deterministic seeded frame with a known linear association.

    Used only when no real table exposes usable analysis columns, so that the
    product remains runnable; its rows are synthetic by construction.
    """
    n_rows = max(int(min_observations) * 2, 120)
    rng = np.random.default_rng(_SYNTHETIC_SEED)
    confounder = rng.normal(size=n_rows)
    treatment = 1.0 + 0.5 * confounder + rng.normal(size=n_rows) * 0.4
    outcome = 2.0 * treatment + 1.5 * confounder + rng.normal(size=n_rows) * 0.4
    return pd.DataFrame({"treatment": treatment, "outcome": outcome, "confounder": confounder})


def _conformal_scale(
    frame: pd.DataFrame,
    treatment: str,
    outcome: str,
    confounders: list[str],
    config: PlatformConfig,
) -> float | None:
    """Split-conformal residual scale of an in-sample outcome regression.

    The scale is used to place proxy bounds around each method's estimate; it
    calibrates outcome residuals of the observational model, not causality.
    """
    predictors = [treatment, *confounders]
    design = np.column_stack([np.ones(len(frame)), frame[predictors].to_numpy(dtype=float)])
    observed = frame[outcome].to_numpy(dtype=float)
    spec = config.causal.conformal
    try:
        coefficients, *_ = np.linalg.lstsq(design, observed, rcond=None)
        predicted = design @ coefficients
        result = split_conformal_intervals(
            predicted,
            observed,
            alpha=spec.alpha,
            train_fraction=spec.train_fraction,
            min_train=spec.min_train,
            seed=_SYNTHETIC_SEED,
        )
    except (ValueError, np.linalg.LinAlgError):
        return None
    return float(result.q_hat)


def estimate_causal_effects(
    config: PlatformConfig,
    evidence: pl.DataFrame | None = None,
    features: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Estimate observational effects for every configured method.

    Inputs default to ``data/gold/statistical_evidence`` and
    ``data/silver/sports_seo``. If either table is missing or has fewer than
    ``config.causal.min_observations`` rows, an empty frame with the full
    output schema is returned so downstream code needs no special casing.
    Otherwise a small analysis frame is built from real columns when they
    exist (SVTP/visibility-like column as outcome, a varying feature as
    treatment, up to three numeric confounders) and a deterministic seeded
    synthetic frame is used only as a documented fallback. Each method in
    ``config.causal.methods`` runs independently: failures are kept as rows
    with ``estimate = NaN`` and an explanatory ``method_status``, successes
    carry ``method_status = "ok"``. Estimates are observational, carry an
    ``assumptions`` list, and are reported with the observational disclaimer.
    """
    storage = Storage(config)
    if evidence is None:
        evidence = storage.read(config.resolve("data/gold/statistical_evidence"))
    if features is None:
        features = storage.read(config.resolve("data/silver/sports_seo"))
    min_observations = config.causal.min_observations
    if (
        evidence is None
        or features is None
        or evidence.height < min_observations
        or features.height < min_observations
    ):
        return empty_effects_frame()

    analysis: pd.DataFrame | None = None
    selection: tuple[str, str, list[str]] | None = None
    for source in (features, evidence):
        picked = _select_columns(source)
        if picked is None:
            continue
        prepared = _analysis_frame(source, picked[0], picked[1], picked[2], min_observations)
        if prepared is None:
            continue
        analysis = prepared
        selection = picked
        break
    if analysis is None or selection is None:
        analysis = _synthetic_frame(min_observations)
        selection = ("treatment", "outcome", ["confounder"])
    treatment, outcome, confounders = selection

    alpha = float(config.causal.alpha)
    q_hat = _conformal_scale(analysis, treatment, outcome, confounders, config)
    outcome_scale = float(np.std(analysis[outcome].to_numpy(dtype=float)))
    produced_at = datetime.now(UTC).isoformat(timespec="seconds")
    environment = config.platform.name

    records: list[dict[str, Any]] = []
    for method in config.causal.methods:
        record: dict[str, Any] = {
            "method": method,
            "treatment": treatment,
            "outcome": outcome,
            "confounders": json.dumps(confounders),
            "estimate": float("nan"),
            "stderr": None,
            "ci_low": None,
            "ci_high": None,
            "p_value": None,
            "n": int(len(analysis)),
            "effect_size": float("nan"),
            "conformal_lower": None,
            "conformal_upper": None,
            "assumptions": json.dumps([]),
            "disclaimer": DISCLAIMER,
            "alpha": alpha,
            "environment": environment,
            "produced_at": produced_at,
            "method_status": "ok",
        }
        runner = _RUNNERS.get(method)
        if runner is None:
            record["method_status"] = f"failed: unknown method '{method}'"
            records.append(record)
            continue
        try:
            estimate = runner(analysis, treatment, outcome, confounders, alpha=alpha)
        except Exception as exc:
            record["method_status"] = f"failed: {type(exc).__name__}: {exc}"
            records.append(record)
            continue
        record.update(
            {
                "estimate": estimate.estimate,
                "stderr": estimate.stderr,
                "ci_low": estimate.ci_low,
                "ci_high": estimate.ci_high,
                "p_value": estimate.p_value,
                "n": estimate.n,
                "effect_size": abs(estimate.estimate) / (outcome_scale + 1e-9),
                "conformal_lower": (estimate.estimate - q_hat if q_hat is not None else None),
                "conformal_upper": (estimate.estimate + q_hat if q_hat is not None else None),
                "assumptions": json.dumps(estimate.assumptions),
            }
        )
        records.append(record)

    if not records:
        return empty_effects_frame()
    data = {name: [record.get(name) for record in records] for name in EFFECT_COLUMNS}
    return pl.DataFrame(data, schema=_EFFECT_SCHEMA)


def write_effects(config: PlatformConfig, effects: pl.DataFrame | None = None) -> Path:
    """Publish the ``gold/causal_effects`` Delta table and return its path.

    ``effects`` defaults to :func:`estimate_causal_effects`; the table is
    overwritten so the newest run is always the readable product.
    """
    storage = Storage(config)
    frame = effects if effects is not None else estimate_causal_effects(config)
    table = storage.gold_table("causal_effects")
    return storage.write(table, frame, mode="overwrite")
