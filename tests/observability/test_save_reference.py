"""Tests for persisting the drift reference snapshot."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.observability.drift import save_reference
from src.platform.config import (
    DriftConfig,
    ObservabilityConfig,
    PlatformConfig,
)


def _config(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(
        repo_root=tmp_path,
        observability=ObservabilityConfig(
            drift=DriftConfig(reference_path="data/silver/_baseline/ref.parquet")
        ),
    )


def test_save_reference_writes_readable_parquet(tmp_path: Path) -> None:
    config = _config(tmp_path)
    frame = pl.DataFrame({"page_id": ["p1", "p2", "p3"], "score": [0.1, 0.2, 0.3]})

    path = save_reference(config, frame)

    assert path.exists()
    assert path == config.resolve(config.observability.drift.reference_path)
    loaded = pl.read_parquet(path)
    assert loaded.shape == (3, 2)
    assert loaded["page_id"].to_list() == ["p1", "p2", "p3"]
