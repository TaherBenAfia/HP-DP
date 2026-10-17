"""Split-conformal calibration tests."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from src.causal.conformal import (
    ConformalResult,
    conformal_correct_svtp,
    split_conformal_intervals,
)

ALPHA = 0.1


def test_empirical_coverage_near_nominal() -> None:
    rng = np.random.default_rng(11)
    n_rows = 1200
    predicted = 3.0 * rng.normal(size=n_rows)
    observed = predicted + rng.normal(size=n_rows)
    result = split_conformal_intervals(
        predicted,
        observed,
        alpha=ALPHA,
        train_fraction=0.5,
        min_train=30,
        seed=0,
    )
    assert isinstance(result, ConformalResult)
    assert result.alpha == ALPHA
    assert result.n_calib == 600
    assert abs(result.empirical_coverage - (1.0 - ALPHA)) <= 0.15
    full_coverage = float(np.mean((observed >= result.lower) & (observed <= result.upper)))
    assert abs(full_coverage - (1.0 - ALPHA)) <= 0.15


def test_q_hat_positive_and_intervals_symmetric() -> None:
    rng = np.random.default_rng(5)
    n_rows = 400
    predicted = rng.normal(size=n_rows)
    observed = predicted + rng.normal(scale=0.5, size=n_rows)
    result = split_conformal_intervals(
        predicted,
        observed,
        alpha=ALPHA,
        train_fraction=0.5,
        min_train=30,
        seed=0,
    )
    assert result.q_hat > 0
    assert np.all(result.lower <= predicted)
    assert np.all(predicted <= result.upper)
    assert np.allclose(result.lower + result.upper, 2.0 * predicted)


def test_min_train_guard_raises() -> None:
    predicted = np.zeros(40)
    observed = np.zeros(40)
    with pytest.raises(ValueError, match="calibration split too small"):
        split_conformal_intervals(
            predicted,
            observed,
            alpha=ALPHA,
            train_fraction=0.5,
            min_train=30,
            seed=0,
        )


def test_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        split_conformal_intervals(
            np.zeros(50),
            np.zeros(40),
            alpha=ALPHA,
            train_fraction=0.5,
            min_train=10,
            seed=0,
        )


def test_conformal_correct_svtp_columns_and_order() -> None:
    rng = np.random.default_rng(7)
    n_rows = 200
    scores = rng.uniform(0.0, 1.0, size=n_rows)
    observed = rng.uniform(0.0, 1.0, size=n_rows)
    frame = conformal_correct_svtp(
        scores,
        observed,
        alpha=ALPHA,
        train_fraction=0.5,
        min_train=30,
        seed=0,
    )
    assert frame.columns == ["svtp_score", "lower", "upper", "calibrated", "inside"]
    assert frame.height == n_rows
    assert frame.schema["inside"] == pl.Boolean
    assert frame.schema["calibrated"] == pl.Boolean
    assert frame["svtp_score"].to_list() == pytest.approx(scores.tolist())
    assert bool(frame["calibrated"].all())
    assert frame.filter(pl.col("lower") <= pl.col("upper")).height == n_rows
    expected_inside = (observed >= frame["lower"].to_numpy()) & (
        observed <= frame["upper"].to_numpy()
    )
    assert frame["inside"].to_list() == expected_inside.tolist()
