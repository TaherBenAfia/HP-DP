"""Tests for Evidently-backed drift detection."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl
import pytest

pytest.importorskip("evidently")

from src.observability.drift import _parse_metrics, compute_drift_report  # noqa: E402
from src.platform.config import (  # noqa: E402
    DriftConfig,
    ObservabilityConfig,
    PlatformConfig,
)


def _config(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(
        repo_root=tmp_path,
        observability=ObservabilityConfig(
            drift=DriftConfig(
                reference_path="ref.parquet",
                report_dir="logs/drift",
                drift_share_threshold=0.3,
            )
        ),
    )


def _frame(seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    return pl.DataFrame(
        {
            "a": rng.normal(0.0, 1.0, 400),
            "b": rng.normal(0.0, 1.0, 400),
            "c": rng.normal(0.0, 1.0, 400),
        }
    )


def test_identical_frames_have_no_drift(tmp_path: Path) -> None:
    config = _config(tmp_path)
    reference = _frame(0)
    report = compute_drift_report(config, reference, reference.clone())

    assert report.share_drifted == pytest.approx(0.0, abs=1e-6)
    assert report.drifted_columns == []
    assert report.dataset_drift is False
    assert report.passed is True


def test_shifted_frame_drifts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    reference = _frame(1)
    current = reference.with_columns(
        [
            (pl.col("a") + 10.0).alias("a"),
            (pl.col("b") * 5.0).alias("b"),
        ]
    )
    report = compute_drift_report(config, reference, current)

    assert report.share_drifted > 0.0
    assert report.passed is False
    assert report.dataset_drift is True
    assert "a" in report.drifted_columns
    assert "b" in report.drifted_columns
    assert report.n_columns == 3


def test_parse_metrics_distance_methods_drift_above_threshold() -> None:
    report = {
        "metrics": [
            {
                "metric_name": "DriftedColumnsCount(drift_share=0.5)",
                "value": {"count": 1.0, "share": 0.5},
            },
            {
                "metric_name": "ValueDrift(column=a,method=Wasserstein distance (normed),threshold=0.1)",
                "value": 0.0,
            },
            {
                "metric_name": "ValueDrift(column=b,method=Wasserstein distance (normed),threshold=0.1)",
                "value": 0.5,
            },
        ]
    }
    share, n_columns, drifted, dataset_drift = _parse_metrics(report)

    assert drifted == ["b"]
    assert n_columns == 2
    assert share == pytest.approx(0.5)
    assert dataset_drift is True


def test_parse_metrics_pvalue_methods_drift_below_threshold() -> None:
    report = {
        "metrics": [
            {
                "metric_name": "ValueDrift(column=a,method=K-S p_value,threshold=0.05)",
                "value": 0.9,
            },
            {
                "metric_name": "ValueDrift(column=b,method=K-S p_value,threshold=0.05)",
                "value": 0.01,
            },
        ]
    }
    share, n_columns, drifted, dataset_drift = _parse_metrics(report)

    assert drifted == ["b"]
    assert n_columns == 2
    assert share == pytest.approx(0.5)
    assert dataset_drift is True
