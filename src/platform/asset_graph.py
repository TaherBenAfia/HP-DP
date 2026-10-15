"""Asset-centric dependency graph with cycle detection and topological sorting.

In this platform, computation is structured as a Directed Acyclic Graph (DAG)
of DataAssets and Tasks rather than rigid sequential execution pipelines.
Tasks consume input assets and produce output assets.
"""

from __future__ import annotations

from typing import Any, Callable
from pydantic import BaseModel, Field


class CycleError(Exception):
    """Raised when a dependency cycle is detected in the asset graph."""
    pass


class DataAsset(BaseModel):
    """A named, versioned data product or dataset in the platform."""

    asset_id: str = Field(..., description="Unique canonical identifier for the asset")
    name: str = Field(..., description="Human-readable asset name")
    layer: str = Field("gold", description="Logical data tier: bronze, silver, gold")
    tenant_id: str = Field("*", description="Owning tenant or '*' if global/shared")
    schema_version: str = Field("1.0.0", description="Contract version of the asset schema")
    description: str = Field("", description="Asset purpose and documentation")


class TaskDefinition(BaseModel):
    """A unit of computation that produces data assets from input assets."""

    task_id: str = Field(..., description="Unique identifier for the task")
    name: str = Field(..., description="Descriptive task title")
    version: str = Field("1.0.0", description="Code/logic version for cache invalidation")
    input_assets: list[str] = Field(default_factory=list, description="IDs of consumed assets")
    output_assets: list[str] = Field(default_factory=list, description="IDs of produced assets")
    is_cacheable: bool = Field(True, description="Whether successful outputs can be cached")
    params: dict[str, Any] = Field(default_factory=dict, description="Execution parameters affecting output")

    # Optional callable compute function: (inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]
    compute_fn: Any = Field(default=None, exclude=True)


class AssetGraph:
    """Directed Acyclic Graph governing data assets and computation tasks."""

    def __init__(self) -> None:
        self._assets: dict[str, DataAsset] = {}
        self._tasks: dict[str, TaskDefinition] = {}
        self._producing_task: dict[str, str] = {}  # output_asset_id -> task_id

    def register_asset(self, asset: DataAsset) -> None:
        """Register a data asset in the graph."""
        self._assets[asset.asset_id] = asset

    def register_task(self, task: TaskDefinition) -> None:
        """Register a task and its dependency edges."""
        self._tasks[task.task_id] = task
        for out_id in task.output_assets:
            self._producing_task[out_id] = task.task_id
        # Verify graph remains acyclic after adding this task
        self.detect_cycles()

    def get_asset(self, asset_id: str) -> DataAsset:
        if asset_id not in self._assets:
            raise KeyError(f"Asset '{asset_id}' is not registered in the graph")
        return self._assets[asset_id]

    def get_task(self, task_id: str) -> TaskDefinition:
        if task_id not in self._tasks:
            raise KeyError(f"Task '{task_id}' is not registered in the graph")
        return self._tasks[task_id]

    def get_task_producing(self, asset_id: str) -> TaskDefinition | None:
        task_id = self._producing_task.get(asset_id)
        return self._tasks[task_id] if task_id else None

    @property
    def assets(self) -> dict[str, DataAsset]:
        return dict(self._assets)

    @property
    def tasks(self) -> dict[str, TaskDefinition]:
        return dict(self._tasks)

    def detect_cycles(self) -> None:
        """Detect cycles across tasks using depth-first search graph coloring."""
        # Nodes: task_ids. Edges: task A -> task B if task A produces an asset that task B inputs.
        # Visited states: 0 = unvisited, 1 = visiting (in current path), 2 = visited
        state: dict[str, int] = {t_id: 0 for t_id in self._tasks}

        def visit(task_id: str, path: list[str]) -> None:
            state[task_id] = 1
            path.append(task_id)

            task = self._tasks[task_id]
            # Successors: any task that consumes an asset produced by this task
            for out_asset in task.output_assets:
                for next_task_id, next_task in self._tasks.items():
                    if out_asset in next_task.input_assets:
                        if state[next_task_id] == 1:
                            cycle_path = " -> ".join(path + [next_task_id])
                            raise CycleError(f"Dependency cycle detected in asset graph: {cycle_path}")
                        if state[next_task_id] == 0:
                            visit(next_task_id, path)

            path.pop()
            state[task_id] = 2

        for t_id in list(self._tasks.keys()):
            if state[t_id] == 0:
                visit(t_id, [])

    def get_ancestor_tasks(self, requested_asset_ids: list[str]) -> list[TaskDefinition]:
        """Return all tasks required to produce the requested assets in topological order."""
        required_tasks: set[str] = set()

        def collect(asset_id: str) -> None:
            producing_task = self.get_task_producing(asset_id)
            if not producing_task:
                return
            if producing_task.task_id not in required_tasks:
                required_tasks.add(producing_task.task_id)
                for in_asset in producing_task.input_assets:
                    collect(in_asset)

        for aid in requested_asset_ids:
            collect(aid)

        # Topologically sort the required tasks
        return self._topological_sort_tasks(required_tasks)

    def _topological_sort_tasks(self, task_subset: set[str]) -> list[TaskDefinition]:
        """Perform topological sort over a subset of tasks."""
        # in_degree: number of prerequisite tasks in task_subset
        in_degree: dict[str, int] = {t_id: 0 for t_id in task_subset}
        adjacency: dict[str, list[str]] = {t_id: [] for t_id in task_subset}

        for t_id in task_subset:
            task = self._tasks[t_id]
            for out_asset in task.output_assets:
                for consumer_id in task_subset:
                    if out_asset in self._tasks[consumer_id].input_assets:
                        adjacency[t_id].append(consumer_id)
                        in_degree[consumer_id] += 1

        queue = [t_id for t_id, deg in in_degree.items() if deg == 0]
        sorted_tasks: list[str] = []

        while queue:
            curr = queue.pop(0)
            sorted_tasks.append(curr)
            for neighbor in adjacency[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_tasks) != len(task_subset):
            raise CycleError("Cycle detected during topological sorting")

        return [self._tasks[t_id] for t_id in sorted_tasks]
