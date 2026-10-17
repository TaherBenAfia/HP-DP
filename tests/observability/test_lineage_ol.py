"""Tests for OpenLineage file-transport emission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("openlineage")

from src.observability.lineage_ol import (  # noqa: E402
    OpenLineageEmitter,
    emit_pipeline_lineage,
)
from src.platform.config import (  # noqa: E402
    DriftConfig,
    GovernanceConfig,
    ObservabilityConfig,
    OpenLineageConfig,
    PlatformConfig,
)


def _config(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(
        repo_root=tmp_path,
        governance=GovernanceConfig(signing_key_path="keys/k.key"),
        observability=ObservabilityConfig(
            openlineage=OpenLineageConfig(file_path="logs/ol.jsonl"),
            drift=DriftConfig(reference_path="ref.parquet", report_dir="logs/drift"),
        ),
    )


def test_emit_start_and_complete(tmp_path: Path) -> None:
    config = _config(tmp_path)
    emitter = OpenLineageEmitter(config)
    assert emitter.client is not None

    emitter.emit_start("pipeline_job", "run-1", outputs=["silver"])
    emitter.emit_complete("pipeline_job", "run-1", outputs=["silver"])

    path = config.resolve(config.observability.openlineage.file_path)
    lines = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) >= 2
    event_types = {line["eventType"] for line in lines}
    assert "START" in event_types
    assert "COMPLETE" in event_types
    assert all(line["job"]["name"] == "pipeline_job" for line in lines)
    assert all(line["job"]["namespace"] == "statseo_local" for line in lines)


def test_emit_pipeline_lineage_counts_events(tmp_path: Path) -> None:
    config = _config(tmp_path)
    count = emit_pipeline_lineage(config, [("job_a", "out_a"), ("job_b", "out_b")])
    assert count == 4


def test_disabled_openlineage_returns_none(tmp_path: Path) -> None:
    config = PlatformConfig(
        repo_root=tmp_path,
        observability=ObservabilityConfig(
            openlineage=OpenLineageConfig(enabled=False, file_path="logs/ol.jsonl")
        ),
    )
    emitter = OpenLineageEmitter(config)
    assert emitter.client is None
    assert emit_pipeline_lineage(config, [("job_a", "out_a")]) == 0
