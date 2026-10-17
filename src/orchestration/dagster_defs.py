"""Dagster asset graph wrapping the sports SEO lakehouse pipeline.

Assets chain Bronze ingestion -> Silver build -> Gold analytics -> Iceberg
publish -> OpenLineage emission, plus a self-contained ``stream_events`` asset
that generates synthetic events and consumes them in-process. Every asset
resolves storage through ``load_platform_config()`` so tests can redirect all
writes to a temporary directory by monkeypatching that function.

Dagster inspects raw ``__annotations__`` when building an asset definition, so
PEP 563 postponed annotations (``from __future__ import annotations``) would be
seen as unresolved strings. The ``_asset`` helper resolves the hints back to
real objects before delegating to ``dagster.asset``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, get_type_hints

import dagster as dg

from src.platform.config import PlatformConfig

DEFAULT_DATASET_ID = "sports_seo_tunisia_v1"
DEFAULT_TOPIC = "seo_observations"
DEFAULT_EVENT_COUNT = 60
DEFAULT_EVENT_SEED = 42


class DatasetConfig(dg.Config):
    """Asset configuration identifying the batch dataset."""

    dataset_id: str = DEFAULT_DATASET_ID


class StreamConfig(dg.Config):
    """Asset configuration for the synthetic streaming slice."""

    event_count: int = DEFAULT_EVENT_COUNT
    seed: int = DEFAULT_EVENT_SEED


def _asset(
    fn: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Wrap ``dagster.asset`` after resolving postponed type hints."""

    def decorator(func: Callable[..., Any]) -> Any:
        func.__annotations__ = get_type_hints(func)
        return dg.asset(**kwargs)(func)

    return decorator(fn) if fn is not None else decorator


def _load_config() -> PlatformConfig:
    from src.platform.config import load_platform_config

    return load_platform_config()


@_asset
def batch_ingest(
    context: dg.AssetExecutionContext,
    config: DatasetConfig,
) -> dg.MaterializeResult:
    """Ingest the raw source dataset into the Bronze layer."""
    from src.ingestion.pipeline import ingest

    result = ingest(config.dataset_id, config=_load_config())
    return dg.MaterializeResult(
        metadata={
            "run_id": result.run_id,
            "dataset_id": result.dataset_id,
            "status": result.status,
            "row_count": result.row_count,
            "bronze_table": result.bronze_table,
            "source_checksum": result.source_checksum,
        }
    )


@_asset(deps=[batch_ingest])
def silver_build(
    context: dg.AssetExecutionContext,
    config: DatasetConfig,
) -> dg.MaterializeResult:
    """Build the normalised, validated Silver table from Bronze."""
    from src.transforms.sports_seo import build_silver

    result = build_silver(config.dataset_id, config=_load_config())
    return dg.MaterializeResult(
        metadata={
            "run_id": result.run_id,
            "dataset_id": result.dataset_id,
            "silver_table": result.silver_table,
            "quarantine_table": result.quarantine_table,
            "row_count": result.row_count,
            "quarantine_rows": result.quarantine_rows,
            "invalid_rows": result.invalid_rows,
            "duplicate_rows": result.duplicate_rows,
        }
    )


@_asset(deps=[silver_build])
def gold_analytics(
    context: dg.AssetExecutionContext,
    config: DatasetConfig,
) -> dg.MaterializeResult:
    """Compute features, statistics and actions, then publish Gold tables."""
    from src.domains.sports_seo.pipeline import run

    result = run(dataset_id=config.dataset_id, config=_load_config())
    return dg.MaterializeResult(
        metadata={
            "run_id": result.run_id,
            "dataset_id": result.dataset_id,
            "domain": result.domain,
            "rows": result.rows,
            "published_tables": len(result.tables),
        }
    )


@_asset(deps=[gold_analytics])
def iceberg_publish(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Publish curated Delta tables into the Iceberg lakehouse catalog."""
    from src.lakehouse.tables import publish_all

    published = publish_all(_load_config())
    return dg.MaterializeResult(
        metadata={
            "table_count": len(published),
            "tables": ", ".join(sorted(published)),
        }
    )


@_asset(deps=[iceberg_publish])
def openlineage_emit(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Emit the OpenLineage completion event for the published pipeline run."""
    import uuid

    from src.observability.lineage_ol import OpenLineageEmitter

    config = _load_config()
    job_name = config.orchestration.dagster.job_name
    run_id = uuid.uuid4().hex
    OpenLineageEmitter(config).emit_complete(
        job_name,
        run_id,
        inputs=["bronze/sports_seo", "silver/sports_seo"],
        outputs=["gold/statistical_evidence", "gold/action_recommendations"],
    )
    return dg.MaterializeResult(metadata={"job_name": job_name, "run_id": run_id})


@_asset
def stream_events(
    context: dg.AssetExecutionContext,
    config: StreamConfig,
) -> dg.MaterializeResult:
    """Generate synthetic events and consume them through an in-memory broker."""
    from src.generators.synthetic import SyntheticEventGenerator
    from src.ingestion.broker import InMemoryBroker
    from src.ingestion.event_consumer import EventConsumer
    from src.platform.metadata_store import MetadataStore

    platform = _load_config()
    broker = InMemoryBroker()
    generator = SyntheticEventGenerator(seed=config.seed)
    events = generator.generate_batch(count=config.event_count)
    broker.publish_batch(DEFAULT_TOPIC, events)

    events_dir = platform.resolve(platform.paths.bronze_dir) / "events"
    consumer = EventConsumer(
        broker=broker,
        metadata_store=MetadataStore(db_path=str(events_dir / "metadata.db")),
        bronze_base_dir=events_dir,
        consumer_id="dagster_stream_consumer",
    )
    report = consumer.process_topic(
        topic=DEFAULT_TOPIC,
        batch_size=config.event_count,
        max_batches=1,
    )
    return dg.MaterializeResult(
        metadata={
            "topic": report.topic,
            "total_read": report.total_read,
            "accepted_count": report.accepted_count,
            "quarantined_count": report.quarantined_count,
            "duplicate_count": report.duplicate_count,
        }
    )


def build_definitions() -> dg.Definitions:
    """Assemble every orchestration asset and the platform's Dagster job."""
    job_name = _load_config().orchestration.dagster.job_name
    return dg.Definitions(
        assets=[
            batch_ingest,
            silver_build,
            gold_analytics,
            iceberg_publish,
            openlineage_emit,
            stream_events,
        ],
        jobs=[dg.define_asset_job(job_name, selection="*")],
    )


defs: dg.Definitions = build_definitions()

__all__ = [
    "DEFAULT_DATASET_ID",
    "DatasetConfig",
    "StreamConfig",
    "batch_ingest",
    "build_definitions",
    "defs",
    "gold_analytics",
    "iceberg_publish",
    "openlineage_emit",
    "silver_build",
    "stream_events",
]
