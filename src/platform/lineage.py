"""First-class data lineage.

Every Silver and Gold artefact records where it came from: source dataset,
source checksum, ingestion run, transformation version and analytical model
version. The store is append-only and idempotent per ``(table, run_id)`` so
that re-running the pipeline never duplicates lineage edges.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from src.platform.config import PlatformConfig, load_platform_config
from src.platform.storage import Storage


class LineageRecord(BaseModel):
    """One node in the lineage graph."""

    table: str
    layer: str
    run_id: str
    dataset_id: str = ""
    dataset_version: str = "0.0.0"
    source_path: str = ""
    source_checksum: str = ""
    parent_tables: list[str] = Field(default_factory=list)
    transformation_version: str = "1.0.0"
    analytical_model_version: str = "1.0.0"
    pipeline_version: str = "1.0.0"
    row_count: int = 0
    produced_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    record_id: str = ""

    def model_post_init(self, __context: Any) -> None:
        """Derive a deterministic id from table + run."""
        if not self.record_id:
            self.record_id = f"{self.table}::{self.run_id}"

    def to_row(self) -> dict[str, Any]:
        """Flatten to a Delta-friendly row, serialising the parent list."""
        payload = self.model_dump()
        payload["parent_tables"] = json.dumps(payload["parent_tables"])
        return payload


class LineageStore:
    """Append-only lineage store backed by a Delta table."""

    def __init__(self, config: PlatformConfig | None = None, storage: Storage | None = None) -> None:
        self.config = config or load_platform_config()
        self.storage = storage or Storage(self.config)

    @property
    def table_path(self) -> Any:
        """Location of the ``gold/lineage`` table."""
        return self.storage.gold_table("lineage")

    def record(self, record: LineageRecord) -> bool:
        """Append a lineage record; returns False when it already existed."""
        frame = pl.DataFrame([record.to_row()])
        existing = self.storage.read(self.table_path)
        if existing is not None and existing.height:
            if record.record_id in existing["record_id"].to_list():
                return False
            frame = pl.concat([existing, frame], how="diagonal_relaxed")
        self.storage.write(self.table_path, frame, mode="overwrite")
        return True

    def records(self) -> pl.DataFrame:
        """All lineage records, newest first."""
        frame = self.storage.read(self.table_path)
        if frame is None:
            return pl.DataFrame()
        return frame.sort("produced_at", descending=True)

    def trace(self, table: str) -> list[dict[str, Any]]:
        """Walk parent links from ``table`` back to the original source.

        The returned list is ordered from the requested table to the root so
        that a reviewer can read the chain top-down:

            recommendation -> action score -> statistical evidence ->
            silver observation -> bronze record -> original source file
        """
        frame = self.records()
        if frame is None or not frame.height:
            return []
        rows = {row["table"]: row for row in frame.to_dicts()}
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        frontier = [table]
        while frontier:
            current = frontier.pop(0)
            if current in seen or current not in rows:
                continue
            seen.add(current)
            row = rows[current]
            chain.append(row)
            frontier.extend(json.loads(row["parent_tables"]))
        return chain

    def describe(self, table: str) -> str:
        """Human-readable lineage chain for the CLI."""
        chain = self.trace(table)
        if not chain:
            return f"no lineage recorded for {table}"
        lines: list[str] = []
        for index, row in enumerate(chain):
            indent = "  " * index
            lines.append(f"{indent}{row['table']}  [{row['layer']}]  run={row['run_id']}")
            if row.get("source_checksum"):
                lines.append(
                    f"{indent}    source={row['source_path']} sha256={row['source_checksum'][:16]}"
                )
        return "\n".join(lines)
