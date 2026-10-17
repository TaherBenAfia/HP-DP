"""Split-conformal calibration for proxy scores.

The Search Visibility Trend Proxy (SVTP) is an observational proxy, not a
causal quantity. The routines here calibrate the *proxy*: they turn absolute
residuals between proxy predictions and observed values into finite-sample
valid prediction intervals. No causal effect is estimated, identified, or
corrected anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(frozen=True)
class ConformalResult:
    """Split-conformal outcome: calibrated scale plus interval diagnostics."""

    alpha: float
    q_hat: float
    n_calib: int
    empirical_coverage: float
    lower: np.ndarray
    upper: np.ndarray


def split_conformal_intervals(
    predicted: np.ndarray,
    observed: np.ndarray,
    *,
    alpha: float,
    train_fraction: float,
    min_train: int,
    seed: int = 0,
) -> ConformalResult:
    """Split-conformal absolute-residual intervals around ``predicted``.

    The inputs are shuffled with ``seed`` and split into a train part of size
    ``floor(n * train_fraction)`` and a calibration remainder. Absolute
    residuals are measured on the calibration split; the finite-sample
    quantile index ``ceil((n_calib + 1) * (1 - alpha))`` (clamped to the
    calibration range) selects ``q_hat``, and every interval is
    ``predicted +/- q_hat``. ``empirical_coverage`` is the share of calibration
    observations inside their own intervals. A calibration split smaller than
    ``min_train`` raises ``ValueError`` because no finite-sample guarantee
    would remain.

    This calibrates predictive residuals of a proxy-style score; it says
    nothing about causality.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must lie strictly between 0 and 1, got {alpha}")
    if not 0.0 < train_fraction < 1.0:
        raise ValueError(f"train_fraction must lie strictly between 0 and 1, got {train_fraction}")
    values = np.asarray(predicted, dtype=float).reshape(-1)
    targets = np.asarray(observed, dtype=float).reshape(-1)
    if values.shape != targets.shape:
        raise ValueError(
            f"predicted and observed must have the same length, "
            f"got {values.size} and {targets.size}"
        )
    if values.size < 2:
        raise ValueError(f"need at least 2 observations, got {values.size}")
    rng = np.random.default_rng(seed)
    order = rng.permutation(values.size)
    n_train = int(np.floor(values.size * train_fraction))
    n_train = max(1, min(n_train, values.size - 1))
    calib_index = order[n_train:]
    n_calib = int(calib_index.size)
    if n_calib < min_train:
        raise ValueError(f"calibration split too small: n_calib={n_calib} < min_train={min_train}")
    residuals = np.abs(targets[calib_index] - values[calib_index])
    rank = int(np.ceil((n_calib + 1) * (1.0 - alpha)))
    index = min(max(rank, 1), n_calib) - 1
    q_hat = float(np.sort(residuals)[index])
    lower = values - q_hat
    upper = values + q_hat
    inside = (targets[calib_index] >= lower[calib_index]) & (
        targets[calib_index] <= upper[calib_index]
    )
    return ConformalResult(
        alpha=alpha,
        q_hat=q_hat,
        n_calib=n_calib,
        empirical_coverage=float(np.mean(inside)),
        lower=lower,
        upper=upper,
    )


def conformal_correct_svtp(
    svtp_scores: np.ndarray,
    observed_visibility: np.ndarray,
    *,
    alpha: float,
    train_fraction: float,
    min_train: int,
    seed: int = 0,
) -> pl.DataFrame:
    """Attach split-conformal bounds to SVTP proxy scores.

    Returns one row per input score with columns ``svtp_score, lower, upper,
    calibrated, inside`` in that order, where ``lower``/``upper`` are the
    conformal bounds around each score and ``inside`` records whether the
    observed visibility falls within them.

    This calibrates the SVTP proxy's agreement with observed visibility; it
    does NOT calibrate, identify, or correct any causal effect, and it must
    never be read as a causal correction.
    """
    result = split_conformal_intervals(
        np.asarray(svtp_scores, dtype=float),
        np.asarray(observed_visibility, dtype=float),
        alpha=alpha,
        train_fraction=train_fraction,
        min_train=min_train,
        seed=seed,
    )
    scores = np.asarray(svtp_scores, dtype=float).reshape(-1)
    observed = np.asarray(observed_visibility, dtype=float).reshape(-1)
    inside = (observed >= result.lower) & (observed <= result.upper)
    return pl.DataFrame(
        {
            "svtp_score": scores,
            "lower": result.lower,
            "upper": result.upper,
            "calibrated": np.ones(scores.size, dtype=bool),
            "inside": inside,
        }
    )
