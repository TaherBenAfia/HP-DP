"""Causal core Gold-product tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from src.causal.core import EFFECT_COLUMNS, estimate_causal_effects, write_effects
from src.causal.estimators import DISCLAIMER
from src.platform.config import CausalConfig, PlatformConfig
from src.platform.storage import Storage


def _tmp_config(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(repo_root=tmp_path, causal=CausalConfig(min_observations=50))


def _synthetic_frames(n_rows: int = 240) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(31)
    confounder = rng.normal(size=n_rows)
    treatment = (0.5 * confounder + rng.normal(size=n_rows) > 0.0).astype(np.int64)
    outcome = 2.0 * treatment.astype(float) + 1.5 * confounder + rng.normal(size=n_rows) * 0.5
    features = pl.DataFrame(
        {
            "svtp_score": outcome,
            "keyword_in_h1": treatment,
            "word_count": (200 + 40 * confounder).astype(np.int64),
            "url_depth": (2 + np.round(confounder)).astype(np.int64),
        }
    )
    evidence = pl.DataFrame(
        {
            "feature": [f"feature_{index}" for index in range(n_rows)],
            "p_value": np.full(n_rows, 0.01),
            "effect_size": np.full(n_rows, 0.2),
        }
    )
    return features, evidence


def test_estimate_returns_required_columns(tmp_path: Path) -> None:
    config = _tmp_config(tmp_path)
    features, evidence = _synthetic_frames()
    effects = estimate_causal_effects(config, evidence=evidence, features=features)
    assert effects.columns == EFFECT_COLUMNS
    assert effects.height == len(config.causal.methods)
    assert effects["method"].to_list() == config.causal.methods
    for row in effects.to_dicts():
        _assert_row(row, config)


def _assert_row(row: dict[str, Any], config: PlatformConfig) -> None:
    assert row["method_status"] == "ok"
    assert np.isfinite(row["estimate"])
    assert np.isfinite(row["effect_size"]) and row["effect_size"] >= 0.0
    assert row["stderr"] is None or row["stderr"] > 0.0
    assert row["n"] >= config.causal.min_observations
    assert row["treatment"] == "keyword_in_h1"
    assert row["outcome"] == "svtp_score"
    assert json.loads(row["confounders"])
    assert json.loads(row["assumptions"])
    assert row["disclaimer"] == DISCLAIMER
    assert row["alpha"] == config.causal.alpha
    assert row["environment"] == config.platform.name
    assert row["produced_at"]
    if row["conformal_lower"] is not None and row["conformal_upper"] is not None:
        assert row["conformal_lower"] <= row["estimate"] <= row["conformal_upper"]


def test_too_few_rows_returns_empty_frame_with_columns(tmp_path: Path) -> None:
    config = _tmp_config(tmp_path)
    features, evidence = _synthetic_frames(n_rows=20)
    effects = estimate_causal_effects(config, evidence=evidence, features=features)
    assert effects.is_empty()
    assert effects.columns == EFFECT_COLUMNS
    assert effects.schema["estimate"] == pl.Float64
    assert effects.schema["assumptions"] == pl.String


def test_missing_tables_return_empty_frame(tmp_path: Path) -> None:
    config = _tmp_config(tmp_path)
    effects = estimate_causal_effects(config)
    assert effects.is_empty()
    assert effects.columns == EFFECT_COLUMNS


def test_unknown_method_is_recorded_as_failed(tmp_path: Path) -> None:
    config = PlatformConfig(
        repo_root=tmp_path,
        causal=CausalConfig(methods=["not_a_method"], min_observations=50),
    )
    features, evidence = _synthetic_frames()
    effects = estimate_causal_effects(config, evidence=evidence, features=features)
    assert effects.height == 1
    row = effects.to_dicts()[0]
    assert row["method_status"].startswith("failed: unknown method")
    assert np.isnan(row["estimate"])
    assert row["disclaimer"] == DISCLAIMER


def test_write_effects_writes_readable_delta_table(tmp_path: Path) -> None:
    config = _tmp_config(tmp_path)
    features, evidence = _synthetic_frames()
    effects = estimate_causal_effects(config, evidence=evidence, features=features)
    path = write_effects(config, effects)
    storage = Storage(config)
    assert path == storage.gold_table("causal_effects")
    assert storage.exists(path)
    loaded = storage.read(path)
    assert loaded is not None
    assert loaded.columns == EFFECT_COLUMNS
    assert loaded.height == effects.height
    assert loaded["method"].to_list() == effects["method"].to_list()


def test_write_effects_computes_when_absent(tmp_path: Path) -> None:
    config = _tmp_config(tmp_path)
    path = write_effects(config)
    storage = Storage(config)
    assert storage.exists(path)
    loaded = storage.read(path)
    assert loaded is not None
    assert loaded.is_empty()
    assert loaded.columns == EFFECT_COLUMNS
