"""Estimator back-end tests on synthetic data with a known linear effect.

Every case uses the same seeded data-generating process with a true linear
effect of ``TRUE_EFFECT``; estimates are observational associations and are
only ever checked against the simulation truth of this synthetic frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.causal.estimators import (
    EffectEstimate,
    doubleml_plr_effect,
    dowhy_linear_effect,
    econml_linear_dml_effect,
)

TRUE_EFFECT = 2.0


def _synthetic(n_rows: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(2024)
    confounder = rng.normal(size=n_rows)
    treatment = 1.0 + 0.5 * confounder + rng.normal(size=n_rows) * 0.4
    outcome = TRUE_EFFECT * treatment + 1.5 * confounder + rng.normal(size=n_rows) * 0.4
    return pd.DataFrame({"treatment": treatment, "outcome": outcome, "confounder": confounder})


def _assert_known_effect(estimate: EffectEstimate, n_rows: int) -> None:
    assert abs(estimate.estimate - TRUE_EFFECT) <= 0.75
    if estimate.ci_low is not None and estimate.ci_high is not None:
        assert estimate.ci_low <= TRUE_EFFECT <= estimate.ci_high
    assert estimate.n == n_rows
    assert estimate.assumptions
    assert estimate.stderr is None or estimate.stderr > 0


def test_dowhy_linear_recovers_known_effect() -> None:
    pytest.importorskip("dowhy")
    data = _synthetic()
    result = dowhy_linear_effect(data, "treatment", "outcome", ["confounder"])
    assert result.method == "dowhy_linear"
    assert result.confounders == ["confounder"]
    assert result.ci_low is not None and result.ci_high is not None
    assert result.stderr is not None
    _assert_known_effect(result, len(data))


def test_econml_linear_dml_recovers_known_effect() -> None:
    pytest.importorskip("econml")
    data = _synthetic()
    result = econml_linear_dml_effect(data, "treatment", "outcome", ["confounder"], alpha=0.05)
    assert result.method == "econml_linear_dml"
    assert result.p_value is None
    _assert_known_effect(result, len(data))


def test_econml_linear_dml_with_effect_modifier() -> None:
    pytest.importorskip("econml")
    data = _synthetic()
    data["modifier"] = (data["confounder"] > 0.0).astype(float)
    result = econml_linear_dml_effect(
        data,
        "treatment",
        "outcome",
        ["confounder"],
        effect_modifier="modifier",
        alpha=0.05,
    )
    _assert_known_effect(result, len(data))


def test_doubleml_plr_recovers_known_effect() -> None:
    pytest.importorskip("doubleml")
    data = _synthetic()
    result = doubleml_plr_effect(data, "treatment", "outcome", ["confounder"], alpha=0.05)
    assert result.method == "doubleml_plr"
    assert result.ci_low is not None and result.ci_high is not None
    assert result.p_value is not None and result.p_value < 0.05
    _assert_known_effect(result, len(data))


def test_doubleml_plr_is_deterministic() -> None:
    pytest.importorskip("doubleml")
    data = _synthetic()
    first = doubleml_plr_effect(data, "treatment", "outcome", ["confounder"])
    second = doubleml_plr_effect(data, "treatment", "outcome", ["confounder"])
    assert first.estimate == second.estimate
    assert first.ci_low == second.ci_low
    assert first.p_value == second.p_value
