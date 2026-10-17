"""Evidently-backed drift detection between Silver snapshots.

Compares a stored reference snapshot against the current Silver table and
reports the share of drifted columns. The Evidently report is parsed into a
typed :class:`DriftReport` so callers never depend on Evidently's schema.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import BaseModel

from src.platform.config import PlatformConfig
from src.platform.storage import Storage

_COLUMN_PATTERN = re.compile(r"column=([^,)]+)")
_THRESHOLD_PATTERN = re.compile(r"threshold=([0-9.eE+-]+)")
_P_VALUE_PATTERN = re.compile(r"p[_-]?value", re.IGNORECASE)
_SILVER_DOMAIN = "sports_seo"


class DriftReport(BaseModel):
    """Typed summary of a drift comparison."""

    share_drifted: float
    n_columns: int
    drifted_columns: list[str]
    dataset_drift: bool
    threshold: float
    passed: bool
    produced_at: str


def _parse_metrics(report_dict: dict[str, Any]) -> tuple[float, int, list[str], bool]:
    """Extract drift share, column count, drifted columns, and dataset flag."""
    metrics = report_dict.get("metrics", []) or []
    share_drifted = 0.0
    drifted_columns: list[str] = []
    n_columns = 0
    preset_drift_share = 0.5
    found_share = False

    for metric in metrics:
        name = str(metric.get("metric_name", ""))
        value = metric.get("value")
        if name.startswith("DriftedColumnsCount"):
            if isinstance(value, dict):
                share_drifted = float(value.get("share", 0.0))
                found_share = True
            config = metric.get("config", {}) or {}
            preset_drift_share = float(config.get("drift_share", preset_drift_share))
        elif name.startswith("ValueDrift"):
            n_columns += 1
            column_match = _COLUMN_PATTERN.search(name)
            if column_match is None:
                continue
            threshold = 0.05
            config = metric.get("config", {}) or {}
            if isinstance(config.get("threshold"), (int, float)):
                threshold = float(config["threshold"])
            else:
                threshold_match = _THRESHOLD_PATTERN.search(name)
                if threshold_match is not None:
                    threshold = float(threshold_match.group(1))
            if not isinstance(value, (int, float)):
                continue
            score = float(value)
            if score != score:  # NaN -> no decision possible
                continue
            # Evidently reports p-values for statistical tests (drift when the
            # value falls below the threshold) but distance scores for large
            # samples (drift when the value rises above the threshold).
            is_p_value = bool(_P_VALUE_PATTERN.search(name))
            drifted = score < threshold if is_p_value else score > threshold
            if drifted:
                drifted_columns.append(column_match.group(1))

    if not found_share and n_columns:
        share_drifted = len(drifted_columns) / n_columns
    dataset_drift = share_drifted > 0.0 and share_drifted >= preset_drift_share
    return share_drifted, n_columns, drifted_columns, dataset_drift


def compute_drift_report(
    config: PlatformConfig, reference: pl.DataFrame, current: pl.DataFrame
) -> DriftReport:
    """Run Evidently's ``DataDriftPreset`` and summarise the result."""
    from evidently import Report
    from evidently.presets import DataDriftPreset

    report = Report(metrics=[DataDriftPreset()])
    snapshot = report.run(
        reference_data=reference.to_pandas(),
        current_data=current.to_pandas(),
    )
    report_dict = snapshot.dict()
    share_drifted, n_columns, drifted_columns, dataset_drift = _parse_metrics(report_dict)

    threshold = float(config.observability.drift.drift_share_threshold)
    return DriftReport(
        share_drifted=share_drifted,
        n_columns=n_columns,
        drifted_columns=drifted_columns,
        dataset_drift=dataset_drift,
        threshold=threshold,
        passed=share_drifted <= threshold,
        produced_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def save_reference(config: PlatformConfig, current: pl.DataFrame | None = None) -> Path:
    """Persist the current Silver snapshot as the drift reference parquet."""
    if current is None:
        storage = Storage(config)
        current = storage.read(storage.silver_table(_SILVER_DOMAIN))
    if current is None:
        current = pl.DataFrame()

    path = config.resolve(config.observability.drift.reference_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    current.write_parquet(path)
    return path


def run_drift_check(config: PlatformConfig) -> DriftReport | None:
    """Compare current Silver against the reference, writing a JSON report.

    Returns ``None`` (after bootstrapping the reference) when no reference
    exists yet.
    """
    reference_path = config.resolve(config.observability.drift.reference_path)
    if not reference_path.exists():
        save_reference(config)
        return None

    storage = Storage(config)
    reference = pl.read_parquet(reference_path)
    current = storage.read(storage.silver_table(_SILVER_DOMAIN))
    if current is None:
        current = pl.DataFrame()

    report = compute_drift_report(config, reference, current)

    report_dir = config.resolve(config.observability.drift.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    output_path = report_dir / f"{timestamp}.json"
    output_path.write_text(
        json.dumps(report.model_dump(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return report
