"""Feast feature-repo generation and online-roundtrip tests."""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
from types import ModuleType

import polars as pl
import pytest

pytest.importorskip("feast")

from src.platform.config import (
    FeatureStoreConfig,
    OlapConfig,
    PlatformConfig,
    SemanticConfig,
)
from src.platform.storage import Storage


@pytest.fixture()
def config(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(
        repo_root=tmp_path,
        feature_store=FeatureStoreConfig(repo_path="feature_repo"),
        semantic=SemanticConfig(project_path="sqlmesh_project", state_path="data/sqlmesh"),
        olap=OlapConfig(enabled=False),
    )


def _write_page_metrics_gold(config: PlatformConfig) -> None:
    storage = Storage(config)
    storage.write(
        storage.gold_table("page_metrics"),
        pl.DataFrame(
            {
                "url": ["https://a.example", "https://b.example"],
                "svtp_score_mean": [0.5, 0.7],
                "serp_position_mean": [3.0, 1.5],
                "observations": [10, 20],
                "product_id": ["p1", "p1"],
                "generated_at": ["2026-01-01T12:00:00", "2026-01-01T12:00:00"],
            }
        ),
    )


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("generated_features", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_feature_repo_writes_repo_files(config: PlatformConfig) -> None:
    from feast import Entity, FeatureService, FeatureView

    from src.feature_store.repo import (
        apply_repo,
        build_feature_repo,
        export_gold_to_parquet,
    )

    _write_page_metrics_gold(config)
    exported = export_gold_to_parquet(config)
    assert [path.name for path in exported] == ["page_metrics.parquet"]

    repo_dir = build_feature_repo(config)
    yaml_path = repo_dir / "feature_store.yaml"
    features_path = repo_dir / "features.py"
    assert yaml_path.exists()
    assert features_path.exists()
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert "provider: local" in yaml_text
    assert "entity_key_serialization_version: 2" in yaml_text
    assert "registry: data/registry.db" in yaml_text
    assert "type: sqlite" in yaml_text
    assert "type: file" in yaml_text

    module = _load_module(features_path)
    entity = module.page_id
    assert isinstance(entity, Entity)
    assert entity.join_key == "page_id"
    view = module.page_metrics_view
    assert isinstance(view, FeatureView)
    field_names = [field.name for field in view.schema]
    assert "svtp_score_mean" in field_names
    assert "product_id" not in field_names
    assert view.source.timestamp_field == "generated_at"
    service = module.gold_features
    assert isinstance(service, FeatureService)

    status = apply_repo(config)
    assert status.startswith("applied"), status


def test_export_derives_entity_and_timestamp(config: PlatformConfig) -> None:
    from src.feature_store.repo import TIMESTAMP_FIELD, export_gold_to_parquet

    _write_page_metrics_gold(config)
    storage = Storage(config)
    storage.write(
        storage.gold_table("visibility_summary"),
        pl.DataFrame({"metric": ["m1", "m2"], "value": [0.5, 0.75]}),
    )

    exported = {path.stem: path for path in export_gold_to_parquet(config)}
    assert set(exported) == {"page_metrics", "visibility_summary"}

    page_frame = pl.read_parquet(exported["page_metrics"])
    assert page_frame.schema["page_id"] == pl.String
    assert page_frame["page_id"].to_list() == ["https://a.example", "https://b.example"]
    assert page_frame.schema["generated_at"].base_type() == pl.Datetime

    visibility_frame = pl.read_parquet(exported["visibility_summary"])
    assert visibility_frame.schema["page_id"] == pl.String
    assert visibility_frame["page_id"].to_list() == ["0", "1"]
    assert TIMESTAMP_FIELD in visibility_frame.columns
    assert visibility_frame.schema[TIMESTAMP_FIELD].base_type() == pl.Datetime


def test_online_roundtrip(config: PlatformConfig) -> None:
    from feast import FeatureStore

    from src.feature_store.repo import (
        apply_repo,
        build_feature_repo,
        export_gold_to_parquet,
        get_online_features,
    )

    _write_page_metrics_gold(config)
    export_gold_to_parquet(config)
    repo_dir = build_feature_repo(config)
    status = apply_repo(config)
    assert status.startswith("applied"), status

    store = FeatureStore(repo_path=str(repo_dir))
    store.materialize(
        dt.datetime(2026, 1, 1),
        dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    online = get_online_features(
        config,
        entity_rows=[{"page_id": "https://a.example"}],
        features=["page_metrics:svtp_score_mean", "page_metrics:observations"],
    )
    assert online["page_id"] == ["https://a.example"]
    assert online["svtp_score_mean"] == [pytest.approx(0.5)]
    assert online["observations"] == [10]
