"""Integration tests for streaming synthetic event ingestion, quarantine routing, and replay safety."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from src.generators.synthetic import SyntheticEventGenerator
from src.ingestion.broker import InMemoryBroker
from src.ingestion.event_consumer import EventConsumer
from src.platform.metadata_store import MetadataStore


def test_end_to_end_synthetic_event_ingestion(tmp_path: Path) -> None:
    """End-to-end slice: synthetic events -> validation -> Bronze Delta + Quarantine."""
    db_path = tmp_path / "metadata.db"
    bronze_dir = tmp_path / "bronze"
    meta_store = MetadataStore(db_path)
    broker = InMemoryBroker()

    consumer = EventConsumer(
        broker=broker,
        metadata_store=meta_store,
        bronze_base_dir=bronze_dir,
        consumer_id="test_consumer_1",
    )

    # Generate 60 events across 2 tenants with intentional duplicates & malformed records
    gen = SyntheticEventGenerator(
        seed=101,
        tenants=["tenant_alpha", "tenant_beta"],
        duplicate_rate=0.15,
        malformed_rate=0.10,
    )
    events = gen.generate_batch(count=60)

    # Publish to broker topic
    topic = "seo_observations"
    broker.publish_batch(topic, events)

    # Consume batch
    report = consumer.process_topic(topic=topic, batch_size=100, max_batches=1)

    assert report.total_read == 60
    assert report.accepted_count > 0
    assert report.quarantined_count > 0
    assert report.duplicate_count >= 0
    assert report.accepted_count + report.quarantined_count + report.duplicate_count == 60

    # 1. Verify Bronze Delta tables per tenant
    for tenant in ["tenant_alpha", "tenant_beta"]:
        tenant_path = bronze_dir / tenant
        if tenant_path.exists():
            dt = DeltaTable(str(tenant_path))
            df = pl.from_arrow(dt.to_pyarrow_table())
            assert isinstance(df, pl.DataFrame)
            assert "_event_id" in df.columns
            assert "_tenant_id" in df.columns
            assert "_ingested_at" in df.columns
            assert "_broker_offset" in df.columns
            # Tenant isolation in storage: each table only contains its own records
            assert df.filter(pl.col("_tenant_id") != tenant).height == 0

    # 2. Verify Quarantine in metadata store
    quarantined = meta_store.get_quarantine_records()
    assert len(quarantined) == report.quarantined_count
    for rec in quarantined:
        assert "quarantine_id" in rec
        assert "error_messages" in rec
        assert len(rec["error_messages"]) > 0
        assert "raw_payload" in rec

    # 3. Verify durable checkpoint
    checkpoint = meta_store.get_checkpoint("test_consumer_1", topic)
    assert checkpoint == 60


def test_idempotent_event_replay(tmp_path: Path) -> None:
    """Replaying previously ingested events must be safe and produce zero duplicate Bronze rows."""
    db_path = tmp_path / "metadata_replay.db"
    bronze_dir = tmp_path / "bronze_replay"
    meta_store = MetadataStore(db_path)
    broker = InMemoryBroker()

    consumer = EventConsumer(
        broker=broker,
        metadata_store=meta_store,
        bronze_base_dir=bronze_dir,
        consumer_id="replay_consumer",
    )

    # Generate 25 clean events
    gen = SyntheticEventGenerator(seed=202, tenants=["tenant_alpha"], duplicate_rate=0.0, malformed_rate=0.0)
    events = gen.generate_batch(count=25)

    topic = "clean_stream"
    broker.publish_batch(topic, events)

    # First run: all 25 accepted
    report1 = consumer.process_topic(topic=topic, batch_size=50)
    assert report1.accepted_count == 25
    assert report1.duplicate_count == 0

    dt = DeltaTable(str(bronze_dir / "tenant_alpha"))
    initial_row_count = pl.from_arrow(dt.to_pyarrow_table()).height
    assert initial_row_count == 25

    # Simulate replay: publish the exact same events again with same event_ids
    broker.publish_batch(topic, events)

    # Second run: all 25 recognized as duplicates
    report2 = consumer.process_topic(topic=topic, batch_size=50)
    assert report2.accepted_count == 0
    assert report2.duplicate_count == 25

    # Verify Bronze Delta table did NOT grow
    dt_after = DeltaTable(str(bronze_dir / "tenant_alpha"))
    after_row_count = pl.from_arrow(dt_after.to_pyarrow_table()).height
    assert after_row_count == 25


def test_consumer_restart_and_checkpoint_recovery(tmp_path: Path) -> None:
    """Consumer recovery after crash resumes processing from last durable checkpoint offset."""
    db_path = tmp_path / "metadata_recovery.db"
    bronze_dir = tmp_path / "bronze_recovery"
    meta_store = MetadataStore(db_path)
    broker = InMemoryBroker()

    # Generate 40 clean events
    gen = SyntheticEventGenerator(seed=303, tenants=["tenant_alpha"], duplicate_rate=0.0, malformed_rate=0.0)
    events = gen.generate_batch(count=40)

    topic = "recovery_stream"
    broker.publish_batch(topic, events)

    # Consumer instance 1 processes first 20 messages
    consumer1 = EventConsumer(
        broker=broker,
        metadata_store=meta_store,
        bronze_base_dir=bronze_dir,
        consumer_id="shared_group",
    )
    report1 = consumer1.process_topic(topic=topic, batch_size=20, max_batches=1)
    assert report1.total_read == 20
    assert report1.accepted_count == 20
    assert meta_store.get_checkpoint("shared_group", topic) == 20

    # Simulate process crash and restart: create fresh Consumer instance 2 with same group
    consumer2 = EventConsumer(
        broker=broker,
        metadata_store=meta_store,
        bronze_base_dir=bronze_dir,
        consumer_id="shared_group",
    )
    report2 = consumer2.process_topic(topic=topic, batch_size=50, max_batches=1)
    assert report2.total_read == 20  # Only reads remaining 20 messages (offset 20 to 40)
    assert report2.accepted_count == 20
    assert meta_store.get_checkpoint("shared_group", topic) == 40

    # Total stored in Bronze is 40
    dt = DeltaTable(str(bronze_dir / "tenant_alpha"))
    total_rows = pl.from_arrow(dt.to_pyarrow_table()).height
    assert total_rows == 40
