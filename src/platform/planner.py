"""Dependency-aware execution planner with deterministic cache key generation.

The planner resolves requested outputs, traverses ancestor dependencies,
computes cache keys accounting for tenant scope, task version, input fingerprints,
and parameters, and determines which tasks must execute vs which valid materializations
can safely be reused.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any
from pydantic import BaseModel, Field

from src.platform.asset_graph import AssetGraph, TaskDefinition
from src.platform.metadata_store import MetadataStore


class PlannedTask(BaseModel):
    """An individual task step in an execution plan."""

    task_id: str
    task_name: str
    tenant_id: str
    version: str
    input_assets: list[str]
    output_assets: list[str]
    cache_key: str
    input_fingerprint: str
    action: str = Field("execute", description="'execute' or 'reuse_cache'")
    cached_materialization_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    """A complete, inspectable execution plan for requested target assets."""

    plan_id: str
    tenant_id: str
    requested_assets: list[str]
    tasks_to_execute: list[PlannedTask]
    tasks_to_reuse: list[PlannedTask]
    ordered_tasks: list[PlannedTask]

    @property
    def total_tasks(self) -> int:
        return len(self.ordered_tasks)

    @property
    def skip_count(self) -> int:
        return len(self.tasks_to_reuse)

    @property
    def execute_count(self) -> int:
        return len(self.tasks_to_execute)


class ExecutionPlanner:
    """Plans dependency-aware execution with selective recomputation."""

    def __init__(self, graph: AssetGraph, metadata_store: MetadataStore) -> None:
        self.graph = graph
        self.metadata_store = metadata_store

    def compute_cache_key(
        self,
        task: TaskDefinition,
        tenant_id: str,
        input_fingerprint: str,
    ) -> str:
        """Calculate deterministic cache key based on computation identity.

        Accounts for:
        - tenant_id
        - task_id and task_version
        - exact input content fingerprint
        - task parameters affecting results
        - output schema versions
        """
        output_schemas = {}
        for out_aid in task.output_assets:
            if out_aid in self.graph.assets:
                output_schemas[out_aid] = self.graph.get_asset(out_aid).schema_version

        key_components = {
            "tenant_id": tenant_id,
            "task_id": task.task_id,
            "task_version": task.version,
            "input_fingerprint": input_fingerprint,
            "params": task.params,
            "output_schemas": output_schemas,
        }
        serialized = json.dumps(key_components, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def plan(
        self,
        requested_assets: list[str],
        tenant_id: str,
        source_fingerprints: dict[str, str] | None = None,
        force_recompute: bool = False,
    ) -> ExecutionPlan:
        """Build an execution plan for the requested assets."""
        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        required_tasks = self.graph.get_ancestor_tasks(requested_assets)

        asset_fingerprints: dict[str, str] = dict(source_fingerprints or {})
        recomputed_assets: set[str] = set()

        tasks_to_execute: list[PlannedTask] = []
        tasks_to_reuse: list[PlannedTask] = []
        ordered_tasks: list[PlannedTask] = []

        with self.metadata_store._get_connection() as conn:
            for task in required_tasks:
                # 1. Compute composite input fingerprint
                input_parts = []
                for in_aid in task.input_assets:
                    fp = asset_fingerprints.get(in_aid, "source_default")
                    input_parts.append(f"{in_aid}:{fp}")
                composite_input_fp = hashlib.sha256("|".join(sorted(input_parts)).encode("utf-8")).hexdigest()

                # 2. Compute task cache key
                cache_key = self.compute_cache_key(
                    task=task,
                    tenant_id=tenant_id,
                    input_fingerprint=composite_input_fp,
                )

                # 3. Check if any upstream asset was recomputed
                upstream_recomputed = any(in_aid in recomputed_assets for in_aid in task.input_assets)

                # 4. Check cache in metadata store if cacheable and not forced
                cached_mat = None
                if task.is_cacheable and not force_recompute and not upstream_recomputed:
                    row = conn.execute(
                        """
                        SELECT materialization_id FROM asset_materializations
                        WHERE cache_key = ? AND tenant_id = ? AND status = 'success'
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (cache_key, tenant_id),
                    ).fetchone()
                    if row:
                        cached_mat = row["materialization_id"]

                # Deterministic output fingerprint for produced assets
                out_fp = hashlib.sha256(f"{task.task_id}:{task.version}:{composite_input_fp}".encode("utf-8")).hexdigest()[:16]

                if cached_mat:
                    planned = PlannedTask(
                        task_id=task.task_id,
                        task_name=task.name,
                        tenant_id=tenant_id,
                        version=task.version,
                        input_assets=task.input_assets,
                        output_assets=task.output_assets,
                        cache_key=cache_key,
                        input_fingerprint=composite_input_fp,
                        action="reuse_cache",
                        cached_materialization_id=cached_mat,
                        params=task.params,
                    )
                    tasks_to_reuse.append(planned)
                    for out_aid in task.output_assets:
                        asset_fingerprints[out_aid] = out_fp
                else:
                    planned = PlannedTask(
                        task_id=task.task_id,
                        task_name=task.name,
                        tenant_id=tenant_id,
                        version=task.version,
                        input_assets=task.input_assets,
                        output_assets=task.output_assets,
                        cache_key=cache_key,
                        input_fingerprint=composite_input_fp,
                        action="execute",
                        cached_materialization_id=None,
                        params=task.params,
                    )
                    tasks_to_execute.append(planned)
                    for out_aid in task.output_assets:
                        recomputed_assets.add(out_aid)
                        asset_fingerprints[out_aid] = out_fp

                ordered_tasks.append(planned)

        return ExecutionPlan(
            plan_id=plan_id,
            tenant_id=tenant_id,
            requested_assets=requested_assets,
            tasks_to_execute=tasks_to_execute,
            tasks_to_reuse=tasks_to_reuse,
            ordered_tasks=ordered_tasks,
        )
