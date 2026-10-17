"""Temporal activities and workflow for the sports SEO lakehouse pipeline.

Activity implementations are plain async functions returning JSON-safe
summaries; they can be awaited directly without a Temporal server, keeping
tests offline and deterministic. ``PipelineWorkflow`` wires the activities
with ``workflow.execute_activity`` for execution on a Temporal worker.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from temporalio import activity, workflow

if TYPE_CHECKING:
    from src.platform.config import PlatformConfig

DEFAULT_DATASET_ID = "sports_seo_tunisia_v1"
DEFAULT_TOPIC = "seo_observations"
DEFAULT_EVENT_COUNT = 50
DEFAULT_EVENT_SEED = 42
DEFAULT_TENANTS = ("tenant_alpha", "tenant_beta")
ACTIVITY_TIMEOUT = timedelta(minutes=10)


def _load_config() -> PlatformConfig:
    from src.platform.config import load_platform_config

    return load_platform_config()


async def _ingest_impl(dataset_id: str = DEFAULT_DATASET_ID) -> dict[str, Any]:
    """Ingest the raw dataset into Bronze and summarise the outcome."""
    from src.ingestion.pipeline import ingest

    result = ingest(dataset_id, config=_load_config())
    return {
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "status": result.status,
        "row_count": result.row_count,
        "bronze_table": result.bronze_table,
        "source_checksum": result.source_checksum,
        "skipped": result.skipped,
    }


async def _silver_impl(dataset_id: str = DEFAULT_DATASET_ID) -> dict[str, Any]:
    """Build the Silver table and summarise quality outcomes."""
    from src.transforms.sports_seo import build_silver

    result = build_silver(dataset_id, config=_load_config())
    return {
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "silver_table": result.silver_table,
        "quarantine_table": result.quarantine_table,
        "row_count": result.row_count,
        "quarantine_rows": result.quarantine_rows,
        "invalid_rows": result.invalid_rows,
        "duplicate_rows": result.duplicate_rows,
        "transform_version": result.transform_version,
    }


async def _analytics_impl(dataset_id: str = DEFAULT_DATASET_ID) -> dict[str, Any]:
    """Run the Gold analytics pipeline and summarise published tables."""
    from src.domains.sports_seo.pipeline import run

    result = run(dataset_id=dataset_id, config=_load_config())
    return {
        "run_id": result.run_id,
        "dataset_id": result.dataset_id,
        "domain": result.domain,
        "rows": result.rows,
        "tables": result.tables,
        "report": result.report,
    }


async def _consume_events_impl(
    count: int = DEFAULT_EVENT_COUNT,
    seed: int = DEFAULT_EVENT_SEED,
    tenants: list[str] | None = None,
) -> dict[str, Any]:
    """Generate synthetic events and consume them through an in-memory broker."""
    from src.generators.synthetic import SyntheticEventGenerator
    from src.ingestion.broker import InMemoryBroker
    from src.ingestion.event_consumer import EventConsumer
    from src.platform.metadata_store import MetadataStore

    tenant_list = list(tenants) if tenants else list(DEFAULT_TENANTS)
    config = _load_config()
    broker = InMemoryBroker()
    generator = SyntheticEventGenerator(seed=seed, tenants=tenant_list)
    events = generator.generate_batch(count=count)
    broker.publish_batch(DEFAULT_TOPIC, events)

    events_dir = config.resolve(config.paths.bronze_dir) / "events"
    consumer = EventConsumer(
        broker=broker,
        metadata_store=MetadataStore(db_path=str(events_dir / "metadata.db")),
        bronze_base_dir=events_dir,
        consumer_id="temporal_stream_consumer",
    )
    report = consumer.process_topic(topic=DEFAULT_TOPIC, batch_size=count, max_batches=1)
    return {
        "topic": report.topic,
        "seed": seed,
        "tenant_count": len(tenant_list),
        "total_read": report.total_read,
        "accepted_count": report.accepted_count,
        "quarantined_count": report.quarantined_count,
        "duplicate_count": report.duplicate_count,
        "last_committed_offset": report.last_committed_offset,
        "status": report.status,
    }


ingest_batch_activity = activity.defn(name="ingest_batch")(_ingest_impl)
silver_batch_activity = activity.defn(name="silver_build")(_silver_impl)
analytics_batch_activity = activity.defn(name="gold_analytics")(_analytics_impl)
consume_events_activity = activity.defn(name="consume_events")(_consume_events_impl)


@workflow.defn
class PipelineWorkflow:
    """End-to-end batch pipeline: ingest -> silver -> gold, plus stream slice."""

    @workflow.run
    async def run(self, dataset_id: str) -> dict[str, Any]:
        ingest_summary = await workflow.execute_activity(
            ingest_batch_activity,
            dataset_id,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
        )
        silver_summary = await workflow.execute_activity(
            silver_batch_activity,
            dataset_id,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
        )
        analytics_summary = await workflow.execute_activity(
            analytics_batch_activity,
            dataset_id,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
        )
        stream_summary = await workflow.execute_activity(
            consume_events_activity,
            DEFAULT_EVENT_COUNT,
            DEFAULT_EVENT_SEED,
            list(DEFAULT_TENANTS),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
        )
        return {
            "dataset_id": dataset_id,
            "ingest": ingest_summary,
            "silver": silver_summary,
            "analytics": analytics_summary,
            "stream": stream_summary,
        }


def worker_kwargs(config: PlatformConfig) -> dict[str, Any]:
    """Keyword arguments for ``temporalio.worker.Worker``."""
    temporal = config.orchestration.temporal
    return {
        "task_queue": temporal.task_queue,
        "workflows": [PipelineWorkflow],
        "activities": [
            ingest_batch_activity,
            silver_batch_activity,
            analytics_batch_activity,
            consume_events_activity,
        ],
    }


def create_client(config: PlatformConfig) -> Any:
    """Return the awaitable ``Client.connect`` coroutine for a configured server."""
    from temporalio.client import Client

    temporal = config.orchestration.temporal
    if not temporal.address:
        raise ValueError("temporal address not configured")
    return Client.connect(temporal.address, namespace=temporal.namespace)


async def connect(config: PlatformConfig) -> Any:
    """Connect to the Temporal server described by ``config``."""
    return await create_client(config)


async def run_workflow(
    config: PlatformConfig,
    dataset_id: str = DEFAULT_DATASET_ID,
) -> dict[str, Any]:
    """Execute ``PipelineWorkflow`` on the configured Temporal server."""
    client = await connect(config)
    temporal = config.orchestration.temporal
    return await client.execute_workflow(
        PipelineWorkflow.run,
        dataset_id,
        id=f"pipeline-{dataset_id}",
        task_queue=temporal.task_queue,
    )
