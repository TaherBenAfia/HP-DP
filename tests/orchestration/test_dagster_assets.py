"""Tests for the Dagster asset graph: definitions wiring and offline materialization."""

from __future__ import annotations

from pathlib import Path

import pytest

dg = pytest.importorskip("dagster")

from src.orchestration.dagster_defs import build_definitions, stream_events  # noqa: E402
from src.platform.config import PlatformConfig, load_platform_config  # noqa: E402

EXPECTED_ASSET_KEYS = {
    "batch_ingest",
    "silver_build",
    "gold_analytics",
    "iceberg_publish",
    "openlineage_emit",
    "stream_events",
}


def test_build_definitions_exposes_assets_and_job() -> None:
    defs = build_definitions()
    assert isinstance(defs, dg.Definitions)
    keys = {spec.key.to_user_string() for spec in defs.resolve_all_asset_specs()}
    assert keys == EXPECTED_ASSET_KEYS
    job_name = load_platform_config().orchestration.dagster.job_name
    job = defs.resolve_job_def(job_name)
    assert job.name == job_name


def test_asset_dependency_edges() -> None:
    defs = build_definitions()
    deps = {
        spec.key.to_user_string(): {dep.asset_key.to_user_string() for dep in spec.deps}
        for spec in defs.resolve_all_asset_specs()
    }
    assert "batch_ingest" in deps["silver_build"]
    assert "silver_build" in deps["gold_analytics"]
    assert "gold_analytics" in deps["iceberg_publish"]
    assert "iceberg_publish" in deps["openlineage_emit"]


def test_stream_events_materializes_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = PlatformConfig(repo_root=tmp_path)
    monkeypatch.setattr(
        "src.platform.config.load_platform_config", lambda *args, **kwargs: config
    )
    result = dg.materialize([stream_events])
    assert result.success
    events_dir = tmp_path / "data" / "bronze" / "events"
    assert events_dir.exists()
    assert (events_dir / "metadata.db").exists()
