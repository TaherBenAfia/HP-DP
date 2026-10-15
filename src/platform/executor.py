"""DAG-aware execution engine recording versioned materializations and execution metrics.

Executes planned tasks in dependency order, skips tasks with valid cache hits,
invokes compute functions with resolved inputs, and writes durable materialization records
into the SQLite metadata store.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Callable
from pydantic import BaseModel, Field

from src.platform.asset_graph import AssetGraph
from src.platform.metadata_store import MetadataStore
from src.platform.planner import ExecutionPlan, PlannedTask


class ExecutionResult(BaseModel):
    """Inspectable outcome of an execution plan."""

    plan_id: str
    tenant_id: str
    status: str
    executed_tasks: list[str] = Field(default_factory=list)
    skipped_tasks: list[str] = Field(default_factory=list)
    materializations: list[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    task_durations: dict[str, float] = Field(default_factory=dict)

    @property
    def cache_hit_ratio(self) -> float:
        total = len(self.executed_tasks) + len(self.skipped_tasks)
        return len(self.skipped_tasks) / total if total > 0 else 0.0


class DAGExecutor:
    """Executes planned tasks, respecting dependencies and recording materializations."""

    def __init__(
        self,
        graph: AssetGraph,
        metadata_store: MetadataStore,
    ) -> None:
        self.graph = graph
        self.metadata_store = metadata_store
        # in-memory store for asset outputs produced during this run or shared
        self._asset_data_store: dict[str, Any] = {}

    def set_asset_data(self, asset_id: str, data: Any) -> None:
        """Seed or store asset data in the executor's workspace."""
        self._asset_data_store[asset_id] = data

    def get_asset_data(self, asset_id: str) -> Any:
        return self._asset_data_store.get(asset_id)

    def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        """Run all tasks in the execution plan."""
        start_time = time.perf_counter()
        executed: list[str] = []
        skipped: list[str] = []
        materialization_ids: list[str] = []
        durations: dict[str, float] = {}

        for planned in plan.ordered_tasks:
            task = self.graph.get_task(planned.task_id)

            if planned.action == "reuse_cache":
                # Skip task computation; reuse cached output
                skipped.append(planned.task_id)
                durations[planned.task_id] = 0.0
                continue

            # Execute task
            t0 = time.perf_counter()
            inputs = {aid: self._asset_data_store.get(aid) for aid in planned.input_assets}

            if task.compute_fn:
                output_data = task.compute_fn(inputs=inputs, params=planned.params)
            else:
                # Default compute dummy output for abstract pipeline steps
                output_data = {out_aid: f"data_for_{out_aid}" for out_aid in planned.output_assets}

            # If output is a dictionary mapping asset_id -> data
            if isinstance(output_data, dict):
                for out_aid in planned.output_assets:
                    if out_aid in output_data:
                        self._asset_data_store[out_aid] = output_data[out_aid]
            else:
                # Single output object assigned to primary output asset
                if planned.output_assets:
                    self._asset_data_store[planned.output_assets[0]] = output_data

            t1 = time.perf_counter()
            task_duration = (t1 - t0) * 1000.0
            durations[planned.task_id] = task_duration
            executed.append(planned.task_id)

            # Record materialization for each produced asset
            now_iso = datetime.now(UTC).isoformat()
            with self.metadata_store._get_connection() as conn:
                for out_aid in planned.output_assets:
                    mat_id = f"mat_{uuid.uuid4().hex[:12]}"
                    materialization_ids.append(mat_id)
                    # Register asset in SQLite if not yet present
                    asset_obj = self.graph.get_asset(out_aid)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO asset_registry (
                            asset_id, tenant_id, asset_name, layer, schema_version, metadata_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            asset_obj.asset_id,
                            planned.tenant_id,
                            asset_obj.name,
                            asset_obj.layer,
                            asset_obj.schema_version,
                            json.dumps({"description": asset_obj.description}),
                            now_iso,
                        ),
                    )

                    # Insert materialization record
                    conn.execute(
                        """
                        INSERT INTO asset_materializations (
                            materialization_id, asset_id, tenant_id, cache_key,
                            input_fingerprint, task_id, task_version, output_uri,
                            created_at, status, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            mat_id,
                            out_aid,
                            planned.tenant_id,
                            planned.cache_key,
                            planned.input_fingerprint,
                            planned.task_id,
                            planned.version,
                            f"asset://{planned.tenant_id}/{out_aid}/{mat_id}",
                            now_iso,
                            "success",
                            json.dumps({"duration_ms": task_duration}),
                        ),
                    )

        total_duration = (time.perf_counter() - start_time) * 1000.0

        return ExecutionResult(
            plan_id=plan.plan_id,
            tenant_id=plan.tenant_id,
            status="success",
            executed_tasks=executed,
            skipped_tasks=skipped,
            materializations=materialization_ids,
            duration_ms=total_duration,
            task_durations=durations,
        )
