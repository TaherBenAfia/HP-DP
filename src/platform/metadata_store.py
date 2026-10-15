"""SQLite-backed metadata store for local-first platform coordination.

This store maintains durable operational metadata:
- Ingestion checkpoints (stream offsets per consumer group / topic / partition)
- Processed event log (event_id dedup index for idempotent processing)
- Quarantine repository (malformed events with diagnostic validation errors)
- Ingestion run statistics (inspectable execution metrics)
- Asset and materialization records (lineage and computation caching)

SQLite in WAL mode provides ACID safety, bounded transactions, and zero
external infrastructure dependencies.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class MetadataStore:
    """Manages SQLite operational metadata for the platform."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            conn.executescript(
                """
                -- Ingestion stream checkpoints
                CREATE TABLE IF NOT EXISTS ingestion_checkpoints (
                    consumer_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    partition_id INTEGER NOT NULL DEFAULT 0,
                    checkpoint_offset INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (consumer_id, topic, partition_id)
                );

                -- Processed events for deduplication and replay safety
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_processed_tenant ON processed_events(tenant_id);

                -- Quarantined invalid records with full diagnostics
                CREATE TABLE IF NOT EXISTS quarantine_records (
                    quarantine_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    quarantined_at TEXT NOT NULL,
                    raw_payload TEXT NOT NULL,
                    error_messages TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_quarantine_tenant ON quarantine_records(tenant_id);
                CREATE INDEX IF NOT EXISTS idx_quarantine_event ON quarantine_records(event_id);

                -- Ingestion batch / run summaries
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    accepted_count INTEGER NOT NULL DEFAULT 0,
                    quarantined_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0
                );

                -- Asset registry definitions (Milestone C+)
                CREATE TABLE IF NOT EXISTS asset_registry (
                    asset_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    asset_name TEXT NOT NULL,
                    layer TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                -- Asset materialization lineage and cache keys (Milestone C/D+)
                CREATE TABLE IF NOT EXISTS asset_materializations (
                    materialization_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_version TEXT NOT NULL,
                    output_uri TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (asset_id) REFERENCES asset_registry(asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mat_cache_key ON asset_materializations(cache_key);
                """
            )

    # -------------------------------------------------------------------------
    # Checkpoint Management
    # -------------------------------------------------------------------------
    def save_checkpoint(
        self,
        consumer_id: str,
        topic: str,
        offset: int,
        partition_id: int = 0,
    ) -> None:
        """Atomically persist consumer offset checkpoint."""
        now = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_checkpoints (consumer_id, topic, partition_id, checkpoint_offset, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(consumer_id, topic, partition_id) DO UPDATE SET
                    checkpoint_offset = excluded.checkpoint_offset,
                    updated_at = excluded.updated_at
                """,
                (consumer_id, topic, partition_id, offset, now),
            )

    def get_checkpoint(
        self,
        consumer_id: str,
        topic: str,
        partition_id: int = 0,
    ) -> int:
        """Retrieve highest acknowledged offset, or 0 if none recorded."""
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT checkpoint_offset FROM ingestion_checkpoints
                WHERE consumer_id = ? AND topic = ? AND partition_id = ?
                """,
                (consumer_id, topic, partition_id),
            ).fetchone()
            return int(row["checkpoint_offset"]) if row else 0

    # -------------------------------------------------------------------------
    # Deduplication & Event Logging
    # -------------------------------------------------------------------------
    def is_event_processed(self, event_id: str) -> bool:
        """Check whether an event_id was already accepted."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return row is not None

    def record_processed_event(
        self,
        event_id: str,
        tenant_id: str,
        source: str,
        status: str = "accepted",
        payload_hash: str = "",
    ) -> bool:
        """Record an event as processed.
        
        Returns True if newly inserted, False if it already existed (duplicate).
        """
        now = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO processed_events (event_id, tenant_id, source, ingested_at, status, payload_hash)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, tenant_id, source, now, status, payload_hash),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    # -------------------------------------------------------------------------
    # Quarantine Store
    # -------------------------------------------------------------------------
    def record_quarantine(
        self,
        quarantine_id: str,
        event_id: str,
        tenant_id: str,
        source: str,
        raw_payload: Any,
        error_messages: list[str] | str,
        schema_version: str = "1.0.0",
    ) -> str:
        """Store an invalid event in quarantine with full diagnostic context."""
        now = datetime.now(UTC).isoformat()
        payload_str = json.dumps(raw_payload) if not isinstance(raw_payload, str) else raw_payload
        errors_str = json.dumps(error_messages) if not isinstance(error_messages, str) else error_messages
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO quarantine_records (
                    quarantine_id, event_id, tenant_id, source, quarantined_at,
                    raw_payload, error_messages, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    quarantine_id,
                    event_id,
                    tenant_id,
                    source,
                    now,
                    payload_str,
                    errors_str,
                    schema_version,
                ),
            )
        return quarantine_id

    def get_quarantine_records(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve quarantined records, optionally filtered by tenant."""
        with self._get_connection() as conn:
            if tenant_id:
                rows = conn.execute(
                    """
                    SELECT * FROM quarantine_records
                    WHERE tenant_id = ?
                    ORDER BY quarantined_at DESC LIMIT ?
                    """,
                    (tenant_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM quarantine_records
                    ORDER BY quarantined_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            result = []
            for r in rows:
                result.append(
                    {
                        "quarantine_id": r["quarantine_id"],
                        "event_id": r["event_id"],
                        "tenant_id": r["tenant_id"],
                        "source": r["source"],
                        "quarantined_at": r["quarantined_at"],
                        "raw_payload": json.loads(r["raw_payload"]),
                        "error_messages": json.loads(r["error_messages"]),
                        "schema_version": r["schema_version"],
                    }
                )
            return result

    # -------------------------------------------------------------------------
    # Ingestion Run Metrics
    # -------------------------------------------------------------------------
    def record_run(
        self,
        run_id: str,
        tenant_id: str,
        accepted_count: int,
        quarantined_count: int,
        duplicate_count: int,
        status: str = "completed",
    ) -> None:
        """Record batch ingestion metrics."""
        now = datetime.now(UTC).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_runs (
                    run_id, tenant_id, started_at, completed_at, status,
                    accepted_count, quarantined_count, duplicate_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    completed_at = excluded.completed_at,
                    status = excluded.status,
                    accepted_count = excluded.accepted_count,
                    quarantined_count = excluded.quarantined_count,
                    duplicate_count = excluded.duplicate_count
                """,
                (
                    run_id,
                    tenant_id,
                    now,
                    now,
                    status,
                    accepted_count,
                    quarantined_count,
                    duplicate_count,
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Retrieve run metrics."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM ingestion_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "run_id": row["run_id"],
                "tenant_id": row["tenant_id"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "status": row["status"],
                "accepted_count": row["accepted_count"],
                "quarantined_count": row["quarantined_count"],
                "duplicate_count": row["duplicate_count"],
            }
