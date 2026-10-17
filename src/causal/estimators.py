"""Observational treatment-outcome effect estimators.

Each estimator returns an :class:`EffectEstimate`: a point estimate of the
association between a treatment and an outcome adjusted for a set of measured
confounders, together with an explicit assumption list. The numbers are
associational summaries under those assumptions, never design-based causal
guarantees. Optional backends (``dowhy``, ``econml``, ``doubleml``) are
imported inside the estimator functions so the module stays importable when a
backend is absent; the caller sees the backend failure as a per-method error.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

DISCLAIMER = "Observational estimate under stated assumptions; not a design-based causal guarantee."


class EffectEstimate(BaseModel):
    """Observational effect estimate with its uncertainty and assumptions."""

    method: str
    treatment: str
    outcome: str
    confounders: list[str]
    estimate: float
    stderr: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    n: int
    assumptions: list[str] = Field(default_factory=list)


def _assumptions(method: str, confounders: Sequence[str]) -> list[str]:
    """Assumption set declared for every row produced by a method."""
    adjustment = "|".join(confounders) if confounders else "none"
    return [
        "no_unmeasured_confounding",
        "positivity_overlap",
        "consistency",
        "iid_sampling",
        f"adjustment_set:{adjustment}",
        f"functional_form:{method}",
    ]


def _finite_frame(data: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Project ``columns`` onto finite numeric values, dropping unusable rows."""
    ordered = list(dict.fromkeys(columns))
    frame = data.loc[:, ordered].apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame.dropna()


def _interval_bounds(value: Any) -> tuple[float | None, float | None]:
    """Flatten a library confidence-interval result into ``(low, high)``."""
    try:
        flat = np.asarray(value, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        return None, None
    if flat.size < 2 or not np.all(np.isfinite(flat[:2])):
        return None, None
    return float(flat[0]), float(flat[1])


def _stderr_from_interval(ci_low: float, ci_high: float, alpha: float) -> float:
    """Convert a two-sided interval back to a normal-approximation standard error."""
    z_value = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    return (ci_high - ci_low) / (2.0 * z_value)


def _first_float(value: Any) -> float | None:
    """First finite entry of a library p-value container, if any."""
    if value is None:
        return None
    try:
        flat = np.asarray(value, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        return None
    if flat.size == 0 or not np.isfinite(flat[0]):
        return None
    return float(flat[0])


def dowhy_linear_effect(
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    confounders: list[str],
    effect_modifier: str | None = None,
    *,
    alpha: float = 0.05,
) -> EffectEstimate:
    """Back-door-adjusted linear-regression association via DoWhy.

    DoWhy identifies the back-door estimand for ``treatment`` on ``outcome``
    given ``confounders`` and fits an OLS adjustment model. The result is an
    observational association under the declared assumptions; the interval is
    read from ``get_confidence_intervals`` at level ``1 - alpha`` and the
    standard error is recovered from that interval with a normal quantile.
    """
    from dowhy import CausalModel

    columns = [treatment, outcome, *confounders]
    if effect_modifier is not None:
        columns.append(effect_modifier)
    frame = _finite_frame(data, columns)
    model = CausalModel(
        data=frame,
        treatment=treatment,
        outcome=outcome,
        common_causes=list(confounders),
        effect_modifiers=[effect_modifier] if effect_modifier is not None else [],
    )
    identified = model.identify_effect(proceed_when_unidentifiable=True)
    estimated = model.estimate_effect(identified, method_name="backdoor.linear_regression")
    try:
        raw_interval = estimated.get_confidence_intervals(confidence_level=1.0 - alpha)
    except Exception:
        raw_interval = None
    ci_low, ci_high = _interval_bounds(raw_interval)
    stderr = (
        _stderr_from_interval(ci_low, ci_high, alpha)
        if ci_low is not None and ci_high is not None
        else None
    )
    return EffectEstimate(
        method="dowhy_linear",
        treatment=treatment,
        outcome=outcome,
        confounders=list(confounders),
        estimate=float(estimated.value),
        stderr=stderr,
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=None,
        n=int(len(frame)),
        assumptions=_assumptions("dowhy_linear", confounders),
    )


def econml_linear_dml_effect(
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    confounders: list[str],
    effect_modifier: str | None = None,
    *,
    alpha: float = 0.05,
) -> EffectEstimate:
    """Double machine-learning linear effect via ``econml.LinearDML``.

    ``LinearDML`` residualises treatment and outcome on the confounders and
    regresses the residuals on each other; the reported quantity is the average
    linear effect across the effect-modifier sample (or the constant effect
    when no modifier is given). The interval comes from ``ate_interval`` at
    level ``1 - alpha``. The estimate is observational under the declared
    assumptions, not a design-based guarantee.
    """
    from econml.dml import LinearDML

    columns = [treatment, outcome, *confounders]
    if effect_modifier is not None:
        columns.append(effect_modifier)
    frame = _finite_frame(data, columns)
    response = frame[outcome].to_numpy(dtype=float)
    assigned = frame[treatment].to_numpy(dtype=float)
    adjustment = frame[list(confounders)].to_numpy(dtype=float) if confounders else None
    modifier = (
        frame[[effect_modifier]].to_numpy(dtype=float) if effect_modifier is not None else None
    )
    model = LinearDML(discrete_treatment=False, random_state=0)
    model.fit(response, assigned, X=modifier, W=adjustment)
    if modifier is None:
        point = float(model.ate())
        low_raw, high_raw = model.ate_interval(alpha=alpha)
    else:
        point = float(np.asarray(model.ate(modifier), dtype=float).reshape(-1)[0])
        low_raw, high_raw = model.ate_interval(modifier, alpha=alpha)
    ci_low, ci_high = float(low_raw), float(high_raw)
    return EffectEstimate(
        method="econml_linear_dml",
        treatment=treatment,
        outcome=outcome,
        confounders=list(confounders),
        estimate=point,
        stderr=_stderr_from_interval(ci_low, ci_high, alpha),
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=None,
        n=int(len(frame)),
        assumptions=_assumptions("econml_linear_dml", confounders),
    )


def doubleml_plr_effect(
    data: pd.DataFrame,
    treatment: str,
    outcome: str,
    confounders: list[str],
    *,
    alpha: float = 0.05,
    seed: int = 0,
) -> EffectEstimate:
    """Partially linear regression (PLR) via ``doubleml.DoubleMLPLR``.

    Nuisance regressions of outcome and treatment on the confounders are fit
    with ordinary least squares and combined by cross-fitting; ``coef`` is the
    de-biased observational association of ``treatment`` with ``outcome``. The
    interval comes from ``confint(level=1 - alpha)`` and the p-value from the
    library's own test statistic. The global NumPy random state is seeded
    around construction and fitting so sample splitting is reproducible.
    """
    from doubleml import DoubleMLData, DoubleMLPLR
    from sklearn.linear_model import LinearRegression

    frame = _finite_frame(data, [treatment, outcome, *confounders])
    response = frame[outcome].to_numpy(dtype=float)
    assigned = frame[treatment].to_numpy(dtype=float)
    adjustment = (
        frame[list(confounders)].to_numpy(dtype=float) if confounders else np.ones((len(frame), 1))
    )
    dml_data = DoubleMLData.from_arrays(x=adjustment, y=response, d=assigned)
    previous_state = np.random.get_state()
    np.random.seed(seed)
    try:
        model = DoubleMLPLR(dml_data, ml_l=LinearRegression(), ml_m=LinearRegression())
        model.fit()
    finally:
        np.random.set_state(previous_state)
    ci_low, ci_high = _interval_bounds(model.confint(level=1.0 - alpha))
    return EffectEstimate(
        method="doubleml_plr",
        treatment=treatment,
        outcome=outcome,
        confounders=list(confounders),
        estimate=float(np.asarray(model.coef, dtype=float).reshape(-1)[0]),
        stderr=float(np.asarray(model.se, dtype=float).reshape(-1)[0]),
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=_first_float(getattr(model, "pval", None)),
        n=int(len(frame)),
        assumptions=_assumptions("doubleml_plr", confounders),
    )
