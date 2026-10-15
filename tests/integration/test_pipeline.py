"""End-to-end pipeline integration tests (Bronze -> Gold)."""

from __future__ import annotations

import json
from typing import Any

import polars as pl
import pytest

from src.domains.sports_seo.actions import FAMILIES
from src.platform.config import load_platform_config
from src.platform.lineage import LineageStore
from src.platform.storage import Storage

DATASET = "sports_seo_tunisia_v1"


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    from src.domains.sports_seo.pipeline import run

    return run(DATASET).report


def test_row_counts(report: dict[str, Any]) -> None:
    assert report["silver_rows"] == 2032
    assert report["valid_rows"] == 1998
    assert report["invalid_rows"] == 34
    assert report["queries"] > 0
    assert report["entities"] > 0


def test_no_fabricated_demand(report: dict[str, Any]) -> None:
    assert report["demand_component_available"] is False


def test_all_tables_published(report: dict[str, Any]) -> None:
    tables = report["published_tables"]
    expected = {
        "entity_metrics",
        "query_metrics",
        "page_metrics",
        "statistical_evidence",
        "action_recommendations",
        "data_quality",
        "quality_summary",
        "observations",
    }
    assert expected <= set(tables)
    storage = Storage(load_platform_config())
    for name, relative in tables.items():
        path = load_platform_config().resolve(relative)
        assert path.exists(), f"{name} missing at {path}"
        assert storage.exists(path), f"{name} is not a Delta table"


def test_statistical_evidence_quality() -> None:
    storage = Storage(load_platform_config())
    evidence = storage.read(storage.gold_table("statistical_evidence"))
    assert evidence is not None and evidence.height > 100
    ok = evidence.filter(pl.col("status") == "ok")
    assert ok.height > 50
    # FDR discipline: significant <=> q <= alpha
    alpha = load_platform_config().statistics.fdr_alpha
    inconsistent = ok.filter(
        pl.col("significant_fdr") & (pl.col("q_value") > alpha)
    ).height + ok.filter(~pl.col("significant_fdr") & (pl.col("q_value") <= alpha)).height
    assert inconsistent == 0
    # no causal language in the published notes
    banned = pl.col("notes").str.contains(r"(?i)\bcauses\b")
    assert ok.filter(banned.fill_null(False)).height == 0


def test_action_recommendations_are_explained() -> None:
    storage = Storage(load_platform_config())
    actions = storage.read(storage.gold_table("action_recommendations"))
    assert actions is not None and actions.height == 1998
    family_names = {family.name for family in FAMILIES}
    assert set(actions["primary_action"].unique().to_list()) <= family_names
    assert actions["primary_action_score"].min() >= 0
    assert actions["primary_action_score"].max() <= 1
    assert set(actions["action_priority"].unique().to_list()) <= {"low", "medium", "high"}
    assert actions["reason"].null_count() == 0
    assert actions["evidence_features"].null_count() == 0
    for value in actions["evidence_features"].head(20):
        payload = json.loads(value)
        assert isinstance(payload, list) and payload


def test_lineage_traces_back_to_source() -> None:
    store = LineageStore(load_platform_config())
    chain = store.trace("gold/action_recommendations")
    tables = [step["table"] for step in chain]
    assert "gold/action_recommendations" in tables
    assert "silver/sports_seo" in tables
    assert "bronze/sports_seo" in tables
    assert chain[0]["source_checksum"]


def test_manifests_written() -> None:
    cfg = load_platform_config()
    manifests = cfg.resolve(cfg.paths.sharing_manifest_dir) / DATASET
    files = sorted(manifests.glob("*.json"))
    assert len(files) >= 10
    payload = json.loads((manifests / "action_recommendations.json").read_text(encoding="utf-8"))
    assert payload["row_count"] == 1998
    assert payload["product_version"] == cfg.data_products.version


def test_pipeline_is_idempotent() -> None:
    from src.domains.sports_seo.pipeline import run

    first = run(DATASET).report
    second = run(DATASET).report
    assert first["valid_rows"] == second["valid_rows"]
    assert first["run_id"] == second["run_id"]
