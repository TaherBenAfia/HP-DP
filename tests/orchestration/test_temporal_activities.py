"""Tests for Temporal activities, worker wiring and client guards (no server)."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("temporalio")

from src.orchestration.temporal_defs import (  # noqa: E402
    PipelineWorkflow,
    _analytics_impl,
    _consume_events_impl,
    _ingest_impl,
    _silver_impl,
    connect,
    create_client,
    worker_kwargs,
)
from src.platform.config import PlatformConfig  # noqa: E402

DATASET = "sports_seo_tunisia_v1"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _redirect_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlatformConfig:
    shutil.copytree(REPO_ROOT / "configs" / "datasources", tmp_path / "configs" / "datasources")
    raw_target = tmp_path / "data" / "raw" / "sports_seo"
    raw_target.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "data" / "raw" / "sports_seo" / "sports_seo_page_features.csv",
        raw_target / "sports_seo_page_features.csv",
    )
    config = PlatformConfig(repo_root=tmp_path)
    monkeypatch.setattr(
        "src.platform.config.load_platform_config", lambda *args, **kwargs: config
    )
    return config


@pytest.fixture(scope="module")
def pipeline_summaries(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    tmp_path = tmp_path_factory.mktemp("temporal_pipeline_repo")
    shutil.copytree(REPO_ROOT / "configs" / "datasources", tmp_path / "configs" / "datasources")
    raw_target = tmp_path / "data" / "raw" / "sports_seo"
    raw_target.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "data" / "raw" / "sports_seo" / "sports_seo_page_features.csv",
        raw_target / "sports_seo_page_features.csv",
    )
    config = PlatformConfig(repo_root=tmp_path)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.platform.config.load_platform_config", lambda *args, **kwargs: config
        )
        return {
            "ingest": asyncio.run(_ingest_impl(DATASET)),
            "silver": asyncio.run(_silver_impl(DATASET)),
            "analytics": asyncio.run(_analytics_impl(DATASET)),
        }


def test_ingest_activity_summary(pipeline_summaries: dict[str, dict[str, Any]]) -> None:
    summary = pipeline_summaries["ingest"]
    expected = {"run_id", "dataset_id", "status", "row_count", "bronze_table", "source_checksum"}
    assert expected <= set(summary)
    assert summary["dataset_id"] == DATASET
    assert summary["status"] in {"success", "skipped"}
    assert summary["row_count"] == 2032
    json.dumps(summary)


def test_silver_activity_summary(pipeline_summaries: dict[str, dict[str, Any]]) -> None:
    summary = pipeline_summaries["silver"]
    expected = {
        "run_id",
        "dataset_id",
        "silver_table",
        "row_count",
        "quarantine_rows",
        "invalid_rows",
    }
    assert expected <= set(summary)
    assert summary["row_count"] == 2032
    assert summary["quarantine_rows"] == 34
    json.dumps(summary)


def test_analytics_activity_summary(pipeline_summaries: dict[str, dict[str, Any]]) -> None:
    summary = pipeline_summaries["analytics"]
    expected = {"run_id", "dataset_id", "domain", "rows", "tables", "report"}
    assert expected <= set(summary)
    assert summary["domain"] == "sports_seo"
    assert summary["rows"] == 1998
    assert isinstance(summary["tables"], dict) and summary["tables"]
    json.dumps(summary)


def test_consume_events_activity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect_to_tmp(tmp_path, monkeypatch)
    summary = asyncio.run(
        _consume_events_impl(count=30, seed=7, tenants=["tenant_alpha", "tenant_beta"])
    )
    expected = {
        "topic",
        "total_read",
        "accepted_count",
        "quarantined_count",
        "duplicate_count",
        "status",
    }
    assert expected <= set(summary)
    assert summary["total_read"] == 30
    assert summary["accepted_count"] > 0
    assert summary["quarantined_count"] > 0
    assert summary["accepted_count"] + summary["quarantined_count"] + summary["duplicate_count"] == 30
    json.dumps(summary)


def test_worker_kwargs_shape() -> None:
    config = PlatformConfig()
    kwargs = worker_kwargs(config)
    assert set(kwargs) == {"task_queue", "workflows", "activities"}
    assert kwargs["task_queue"] == config.orchestration.temporal.task_queue
    assert kwargs["workflows"] == [PipelineWorkflow]
    assert len(kwargs["activities"]) == 4


def test_create_client_and_connect_require_address() -> None:
    config = PlatformConfig()
    assert config.orchestration.temporal.address == ""
    with pytest.raises(ValueError, match="temporal address not configured"):
        create_client(config)
    with pytest.raises(ValueError, match="temporal address not configured"):
        asyncio.run(connect(config))
