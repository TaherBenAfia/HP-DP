"""Integration tests for DAG execution, caching, selective recomputation, and tenant isolation."""

from __future__ import annotations

from pathlib import Path

from src.platform.asset_graph import AssetGraph, DataAsset, TaskDefinition
from src.platform.executor import DAGExecutor
from src.platform.metadata_store import MetadataStore
from src.platform.planner import ExecutionPlanner


def test_dag_caching_and_selective_recomputation(tmp_path: Path) -> None:
    """End-to-end test of DAG planning, cache reuse, invalidation, and tenant isolation."""
    db_path = tmp_path / "metadata_dag.db"
    meta = MetadataStore(db_path)
    graph = AssetGraph()

    # Register assets
    graph.register_asset(DataAsset(asset_id="bronze_pages", name="Bronze Pages", layer="bronze"))
    graph.register_asset(DataAsset(asset_id="silver_cleaned", name="Silver Cleaned", layer="silver"))
    graph.register_asset(DataAsset(asset_id="gold_metrics", name="Gold Metrics", layer="gold"))
    graph.register_asset(DataAsset(asset_id="unrelated_bronze", name="Unrelated Bronze", layer="bronze"))
    graph.register_asset(DataAsset(asset_id="unrelated_gold", name="Unrelated Gold", layer="gold"))

    # Register compute functions
    execution_counter = {"clean": 0, "metrics": 0, "unrelated": 0}

    def compute_clean(inputs: dict, params: dict) -> dict:
        execution_counter["clean"] += 1
        return {"silver_cleaned": "cleaned_page_data"}

    def compute_metrics(inputs: dict, params: dict) -> dict:
        execution_counter["metrics"] += 1
        return {"gold_metrics": "computed_metrics_scores"}

    def compute_unrelated(inputs: dict, params: dict) -> dict:
        execution_counter["unrelated"] += 1
        return {"unrelated_gold": "unrelated_output"}

    # Register tasks
    task_clean = TaskDefinition(
        task_id="clean_task",
        name="Clean Pages",
        version="1.0.0",
        input_assets=["bronze_pages"],
        output_assets=["silver_cleaned"],
        compute_fn=compute_clean,
    )
    task_metrics = TaskDefinition(
        task_id="metrics_task",
        name="Compute Metrics",
        version="1.0.0",
        input_assets=["silver_cleaned"],
        output_assets=["gold_metrics"],
        compute_fn=compute_metrics,
    )
    task_unrelated = TaskDefinition(
        task_id="unrelated_task",
        name="Unrelated Task",
        version="1.0.0",
        input_assets=["unrelated_bronze"],
        output_assets=["unrelated_gold"],
        compute_fn=compute_unrelated,
    )

    graph.register_task(task_clean)
    graph.register_task(task_metrics)
    graph.register_task(task_unrelated)

    planner = ExecutionPlanner(graph=graph, metadata_store=meta)
    executor = DAGExecutor(graph=graph, metadata_store=meta)

    # -------------------------------------------------------------------------
    # RUN 1: Cold start for tenant_alpha
    # -------------------------------------------------------------------------
    plan1 = planner.plan(
        requested_assets=["gold_metrics", "unrelated_gold"],
        tenant_id="tenant_alpha",
        source_fingerprints={"bronze_pages": "fp_v1", "unrelated_bronze": "unrel_v1"},
    )
    assert plan1.execute_count == 3
    assert plan1.skip_count == 0

    res1 = executor.execute(plan1)
    assert set(res1.executed_tasks) == {"clean_task", "metrics_task", "unrelated_task"}
    assert res1.executed_tasks.index("clean_task") < res1.executed_tasks.index("metrics_task")
    assert res1.skipped_tasks == []
    assert execution_counter["clean"] == 1
    assert execution_counter["metrics"] == 1
    assert execution_counter["unrelated"] == 1

    # -------------------------------------------------------------------------
    # RUN 2: Re-run identical request -> 100% cache hit
    # -------------------------------------------------------------------------
    plan2 = planner.plan(
        requested_assets=["gold_metrics", "unrelated_gold"],
        tenant_id="tenant_alpha",
        source_fingerprints={"bronze_pages": "fp_v1", "unrelated_bronze": "unrel_v1"},
    )
    assert plan2.execute_count == 0
    assert plan2.skip_count == 3

    res2 = executor.execute(plan2)
    assert res2.executed_tasks == []
    assert set(res2.skipped_tasks) == {"clean_task", "metrics_task", "unrelated_task"}
    assert res2.cache_hit_ratio == 1.0
    # Counters did NOT increment
    assert execution_counter["clean"] == 1
    assert execution_counter["metrics"] == 1
    assert execution_counter["unrelated"] == 1

    # -------------------------------------------------------------------------
    # RUN 3: Input change on bronze_pages only -> selective recomputation!
    # SEO branch recomputes, unrelated branch remains cached!
    # -------------------------------------------------------------------------
    plan3 = planner.plan(
        requested_assets=["gold_metrics", "unrelated_gold"],
        tenant_id="tenant_alpha",
        source_fingerprints={"bronze_pages": "fp_v2_NEW", "unrelated_bronze": "unrel_v1"},
    )
    assert plan3.execute_count == 2  # clean_task & metrics_task
    assert plan3.skip_count == 1     # unrelated_task reused!

    res3 = executor.execute(plan3)
    assert "clean_task" in res3.executed_tasks
    assert "metrics_task" in res3.executed_tasks
    assert "unrelated_task" in res3.skipped_tasks
    assert execution_counter["clean"] == 2
    assert execution_counter["metrics"] == 2
    assert execution_counter["unrelated"] == 1  # Still 1!

    # -------------------------------------------------------------------------
    # RUN 4: Multi-tenant isolation: identical inputs for tenant_beta must NOT
    # hit tenant_alpha's cache!
    # -------------------------------------------------------------------------
    plan4 = planner.plan(
        requested_assets=["gold_metrics"],
        tenant_id="tenant_beta",  # Different tenant
        source_fingerprints={"bronze_pages": "fp_v1"},
    )
    assert plan4.execute_count == 2  # Must execute for tenant_beta!
    assert plan4.skip_count == 0

    res4 = executor.execute(plan4)
    assert res4.executed_tasks == ["clean_task", "metrics_task"]
    assert execution_counter["clean"] == 3
    assert execution_counter["metrics"] == 3
