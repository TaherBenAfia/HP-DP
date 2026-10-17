"""SQLMesh project generation, parsing and run tests."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

pytest.importorskip("sqlmesh")

from src.platform.config import OlapConfig, PlatformConfig, SemanticConfig


def _config(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(
        repo_root=tmp_path,
        semantic=SemanticConfig(project_path="sqlmesh_project", state_path="data/sqlmesh"),
        olap=OlapConfig(enabled=False),
    )


def _write_gold(config: PlatformConfig) -> None:
    gold = config.resolve(config.paths.gold_dir)
    datasets = {
        "statistical_evidence": pl.DataFrame(
            {"entity": ["a", "b"], "effect": [0.1, 0.2], "p_value": [0.01, 0.04]}
        ),
        "action_recommendations": pl.DataFrame(
            {"url": ["https://a.example", "https://b.example"], "primary_action": ["x", "y"]}
        ),
        "visibility_summary": pl.DataFrame(
            {"metric": ["m1", "m2", "m2"], "value": [0.5, 0.6, 0.8]}
        ),
    }
    for table, frame in datasets.items():
        table_dir = gold / table
        table_dir.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(table_dir / "part-0.parquet")


def test_build_project_writes_config_and_models(tmp_path: Path) -> None:
    from src.semantic.mesh import build_project

    config = _config(tmp_path)
    project = build_project(config)
    assert project == tmp_path / "sqlmesh_project"
    assert (project / "config.yaml").exists()

    expected = [
        "gold_statistical_evidence.sql",
        "gold_action_recommendations.sql",
        "metrics_visibility.sql",
    ]
    model_files = sorted(path.name for path in (project / "models").glob("*.sql"))
    assert model_files == sorted(expected)

    config_text = (project / "config.yaml").read_text(encoding="utf-8")
    assert "localduck" in config_text
    assert "type: duckdb" in config_text
    assert "dialect: duckdb" in config_text

    model_text = (project / "models" / "metrics_visibility.sql").read_text(encoding="utf-8")
    assert "kind FULL" in model_text
    assert "read_parquet(" in model_text
    assert "visibility_summary/*.parquet" in model_text.replace("\\", "/")


def test_model_names_parse(tmp_path: Path) -> None:
    from src.semantic.mesh import build_project, model_names

    config = _config(tmp_path)
    build_project(config)
    names = model_names(config)
    assert any("gold_statistical_evidence" in name for name in names)
    assert any("gold_action_recommendations" in name for name in names)
    assert any("metrics_visibility" in name for name in names)


def test_run_semantic_layer_end_to_end(tmp_path: Path) -> None:
    import duckdb

    from src.semantic.mesh import run_semantic_layer

    config = _config(tmp_path)
    _write_gold(config)
    summary = run_semantic_layer(config, "dev")
    assert "dev" in summary

    duckdb_path = config.resolve(config.paths.duckdb_path)
    connection = duckdb.connect(str(duckdb_path))
    try:
        rows = connection.execute(
            "SELECT metric, avg_value FROM statseo__dev.metrics_visibility ORDER BY metric"
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in rows] == ["m1", "m2"]
    assert float(rows[1][1]) == pytest.approx(0.7)
