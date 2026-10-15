"""Event ingestion consumer with schema validation, quarantine routing, and replay safety.

This consumer processes events from an EventBroker, guarantees idempotent processing
via stable event_id deduplication, routes invalid events to quarantine with full diagnostic
error tracking, persists accepted events to Bronze Delta tables with provenance,
and commits durable checkpoints to the metadata store.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from deltalake import write_deltalake
from pydantic import BaseModel, Field

from src.ingestion.broker import EventBroker
from src.ingestion.event_schema import validate_event
from src.platform.metadata_store import MetadataStore


class IngestionReport(BaseModel):
    """Execution summary of an event ingestion run."""

    run_id: str
    consumer_id: str
    topic: str
    total_read: int = 0
    accepted_count: int = 0
    quarantined_count: int = 0
    duplicate_count: int = 0
    last_committed_offset: int = 0
    started_at: datetime
    completed_at: datetime
    status: str = "success"


class EventConsumer:
    """Consumes and persists analytical event streams into Bronze storage."""

    def __init__(
        self,
        broker: EventBroker,
        metadata_store: MetadataStore,
        bronze_base_dir: str | Path = "data/bronze/events",
        consumer_id: str = "bronze_event_consumer",
    ) -> None:
        self.broker = broker
        self.metadata_store = metadata_store
        self.bronze_base_dir = Path(bronze_base_dir)
        self.consumer_id = consumer_id

    def process_topic(
        self,
        topic: str,
        batch_size: int = 100,
        max_batches: int = 1,
    ) -> IngestionReport:
        """Consume and process batches of events from a topic."""
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC)

        total_read = 0
        total_accepted = 0
        total_quarantined = 0
        total_duplicate = 0

        last_offset = self.metadata_store.get_checkpoint(self.consumer_id, topic)

        for _ in range(max_batches):
            messages = self.broker.consume(topic, self.consumer_id, limit=batch_size)
            if not messages:
                break

            accepted_by_tenant: dict[str, list[dict[str, Any]]] = {}

            for offset, raw_msg in messages:
                total_read += 1
                outcome = validate_event(raw_msg)

                if not outcome.is_valid:
                    # Route to quarantine
                    event_id = raw_msg.get("event_id", f"unknown_{uuid.uuid4().hex[:8]}")
                    tenant_id = str(raw_msg.get("tenant_id", "unknown")).lower()
                    source = str(raw_msg.get("source", "unknown"))
                    schema_ver = str(raw_msg.get("schema_version", "unknown"))
                    quarantine_id = f"quar_{uuid.uuid4().hex[:12]}"

                    self.metadata_store.record_quarantine(
                        quarantine_id=quarantine_id,
                        event_id=event_id,
                        tenant_id=tenant_id,
                        source=source,
                        raw_payload=raw_msg,
                        error_messages=outcome.error_messages,
                        schema_version=schema_ver,
                    )
                    total_quarantined += 1
                    last_offset = offset + 1
                    continue

                envelope = outcome.envelope
                assert envelope is not None
                assert outcome.validated_payload is not None

                # Deduplication check
                if self.metadata_store.is_event_processed(envelope.event_id):
                    total_duplicate += 1
                    last_offset = offset + 1
                    continue

                # Prepare bronze record with provenance
                payload_json = json.dumps(outcome.validated_payload, sort_keys=True)
                payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

                now_iso = datetime.now(UTC).isoformat()
                bronze_record = dict(outcome.validated_payload)
                bronze_record.update(
                    {
                        "_event_id": envelope.event_id,
                        "_tenant_id": envelope.tenant_id,
                        "_source": envelope.source,
                        "_event_type": envelope.event_type,
                        "_schema_version": envelope.schema_version,
                        "_event_timestamp": envelope.timestamp.isoformat(),
                        "_ingested_at": now_iso,
                        "_broker_offset": offset,
                        "_payload_hash": payload_hash,
                    }
                )

                # Record in dedup index
                inserted = self.metadata_store.record_processed_event(
                    event_id=envelope.event_id,
                    tenant_id=envelope.tenant_id,
                    source=envelope.source,
                    status="accepted",
                    payload_hash=payload_hash,
                )
                if not inserted:
                    total_duplicate += 1
                    last_offset = offset + 1
                    continue

                tenant = envelope.tenant_id
                if tenant not in accepted_by_tenant:
                    accepted_by_tenant[tenant] = []
                accepted_by_tenant[tenant].append(bronze_record)
                total_accepted += 1
                last_offset = offset + 1

            # Persist accepted records per tenant into Bronze tables
            for tenant, records in accepted_by_tenant.items():
                self._persist_bronze_records(tenant=tenant, records=records)

            # Commit checkpoint after successful persistence
            self.metadata_store.save_checkpoint(
                consumer_id=self.consumer_id,
                topic=topic,
                offset=last_offset,
            )
            self.broker.commit(self.consumer_id, topic, last_offset)

        completed_at = datetime.now(UTC)

        # Record run summary
        self.metadata_store.record_run(
            run_id=run_id,
            tenant_id="multi",
            accepted_count=total_accepted,
            quarantined_count=total_quarantined,
            duplicate_count=total_duplicate,
            status="success",
        )

        return IngestionReport(
            run_id=run_id,
            consumer_id=self.consumer_id,
            topic=topic,
            total_read=total_read,
            accepted_count=total_accepted,
            quarantined_count=total_quarantined,
            duplicate_count=total_duplicate,
            last_committed_offset=last_offset,
            started_at=started_at,
            completed_at=completed_at,
            status="success",
        )

    def _persist_bronze_records(self, tenant: str, records: list[dict[str, Any]]) -> None:
        """Write accepted records into a tenant-partitioned Bronze Delta table."""
        if not records:
            return
        df = pl.DataFrame(records)
        target_path = self.bronze_base_dir / tenant
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Write / append Delta table
        write_deltalake(
            str(target_path),
            df.to_arrow(),
            mode="append",
        )
