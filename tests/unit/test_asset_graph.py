"""Unit tests for the AssetGraph, cycle detection, and dependency resolution."""

import pytest
from src.platform.asset_graph import AssetGraph, CycleError, DataAsset, TaskDefinition


def test_asset_and_task_registration() -> None:
    """Assets and tasks can be registered and retrieved."""
    graph = AssetGraph()
    graph.register_asset(DataAsset(asset_id="a1", name="Asset 1", layer="bronze"))
    graph.register_asset(DataAsset(asset_id="a2", name="Asset 2", layer="silver"))

    graph.register_task(
        TaskDefinition(
            task_id="t1",
            name="Task 1",
            input_assets=["a1"],
            output_assets=["a2"],
        )
    )

    assert graph.get_asset("a1").name == "Asset 1"
    assert graph.get_task("t1").name == "Task 1"
    assert graph.get_task_producing("a2").task_id == "t1"


def test_cycle_detection() -> None:
    """Graph must reject dependency cycles with CycleError."""
    graph = AssetGraph()
    graph.register_asset(DataAsset(asset_id="a1", name="A1"))
    graph.register_asset(DataAsset(asset_id="a2", name="A2"))

    # t1: a1 -> a2
    graph.register_task(
        TaskDefinition(task_id="t1", name="T1", input_assets=["a1"], output_assets=["a2"])
    )

    # t2: a2 -> a1 (creates cycle a1 -> a2 -> a1)
    with pytest.raises(CycleError, match="Dependency cycle detected"):
        graph.register_task(
            TaskDefinition(task_id="t2", name="T2", input_assets=["a2"], output_assets=["a1"])
        )


def test_topological_ancestor_resolution() -> None:
    """Ancestor task resolution returns prerequisites in topological execution order."""
    graph = AssetGraph()
    for i in range(1, 5):
        graph.register_asset(DataAsset(asset_id=f"a{i}", name=f"A{i}"))

    # t1: a1 -> a2
    graph.register_task(TaskDefinition(task_id="t1", name="T1", input_assets=["a1"], output_assets=["a2"]))
    # t2: a2 -> a3
    graph.register_task(TaskDefinition(task_id="t2", name="T2", input_assets=["a2"], output_assets=["a3"]))
    # t3: a3 -> a4
    graph.register_task(TaskDefinition(task_id="t3", name="T3", input_assets=["a3"], output_assets=["a4"]))
    # t_unrelated: produces a_other
    graph.register_asset(DataAsset(asset_id="a_other", name="Other"))
    graph.register_task(TaskDefinition(task_id="t_other", name="Other", input_assets=[], output_assets=["a_other"]))

    # Requesting a4 should only pull t1, t2, t3 in that order
    ancestors = graph.get_ancestor_tasks(["a4"])
    ancestor_ids = [t.task_id for t in ancestors]

    assert ancestor_ids == ["t1", "t2", "t3"]
    assert "t_other" not in ancestor_ids
