"""Statistical engine tests."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from src.analytics.feature_registry import FeatureKind, FeatureRegistry, FeatureSpec
from src.statistics.engine import (
    benjamini_hochberg,
    chi_square_test,
    mann_whitney_test,
    run_tests,
    spearman_test,
)


def test_benjamini_hochberg_controls_fdr() -> None:
    p_values = [0.001, 0.004, 0.03, 0.04, 0.5, 0.9]
    q_values, rejected = benjamini_hochberg(p_values, alpha=0.05)
    assert len(q_values) == len(p_values)
    assert all(q >= p for q, p in zip(q_values, p_values, strict=True))
    assert rejected[0] is True
    assert rejected[-1] is False
    # monotone adjustment
    assert all(a <= b + 1e-12 for a, b in zip(q_values, q_values[1:], strict=False))


def test_benjamini_hochberg_empty() -> None:
    assert benjamini_hochberg([]) == ([], [])


def test_spearman_detects_monotone_relation() -> None:
    x = pl.Series("x", range(50))
    y = pl.Series("y", [value * 2 for value in range(50)])
    result = spearman_test(x, y, "x", "family", "outcome")
    assert result.status == "ok"
    assert result.statistic == pytest.approx(1.0)
    assert result.p_value < 0.001
    assert result.direction == "positive"


def test_spearman_skips_constant_feature() -> None:
    x = pl.Series("x", [5] * 40)
    y = pl.Series("y", list(range(40)))
    result = spearman_test(x, y, "x", "family", "outcome")
    assert result.status == "skipped"
    assert "zero variance" in result.notes


def test_spearman_skips_small_samples() -> None:
    x = pl.Series("x", [1, 2])
    y = pl.Series("y", [1, 2])
    assert spearman_test(x, y, "x", "f", "o").status == "skipped"


def test_mann_whitney_separates_classes() -> None:
    values = pl.Series("x", [1.0] * 20 + [10.0] * 20)
    groups = pl.Series("g", [0] * 20 + [1] * 20)
    result = mann_whitney_test(values, groups, "x", "family", "page_trend")
    assert result.status == "ok"
    assert result.p_value < 0.001
    assert result.effect_size_type == "rank_biserial"
    assert result.effect_size == pytest.approx(1.0)


def test_mann_whitney_rejects_tiny_classes() -> None:
    values = pl.Series("x", [1.0, 2.0, 3.0, 4.0])
    groups = pl.Series("g", [1, 1, 0, 0])
    assert mann_whitney_test(values, groups, "x", "f", "o").status == "skipped"


def test_chi_square_finds_association() -> None:
    feature = pl.Series("flag", [1] * 30 + [0] * 10 + [0] * 30 + [1] * 10)
    outcome = pl.Series("g", [1] * 40 + [0] * 40)
    result = chi_square_test(feature, outcome, "flag", "family", "page_trend")
    assert result.status == "ok"
    assert result.p_value < 0.01
    assert result.effect_size_type == "cramers_v"
    assert 0 <= result.effect_size <= 1
    # presence is more frequent in class 1 for the first block
    assert result.direction == "positive"


def test_run_tests_reports_every_feature() -> None:
    rng = np.random.default_rng(7)
    frame = pl.DataFrame(
        {
            "svtp_score": rng.random(120),
            "page_trend": (rng.random(120) > 0.5).astype(int),
            "signal": rng.random(120),
            "constant": [1.0] * 120,
        }
    )
    registry = FeatureRegistry(
        [
            FeatureSpec(name="signal", family="content", kind=FeatureKind.NUMERIC),
            FeatureSpec(name="constant", family="content", kind=FeatureKind.NUMERIC),
            FeatureSpec(name="missing", family="content", kind=FeatureKind.NUMERIC),
        ]
    )
    result = run_tests(frame, registry.all, outcome="svtp_score", binary_outcome="page_trend")
    statuses = dict(zip(result["feature"].to_list(), result["status"].to_list(), strict=True))
    assert statuses["signal"] == "ok"
    assert statuses["constant"] == "skipped"
    assert statuses["missing"] == "skipped"
    # q-values only assigned to tested rows
    for row in result.filter(pl.col("status") == "ok").iter_rows(named=True):
        assert row["q_value"] is not None
        assert row["q_value"] >= row["p_value"]
