"""Publish Delta tables from local storage into the Iceberg catalog.

Sources stay in Delta under the repository; the lakehouse copy is an Iceberg
table registered in the configured catalog, so downstream consumers can query
snapshotted, versioned data while the Delta tables remain the system of record.
"""

from __future__ import annotations

from deltalake import DeltaTable

from src.lakehouse.catalog import create_catalog, default_table_name
from src.platform.config import PlatformConfig
from src.platform.storage import Storage


def publish_delta_table(
    config: PlatformConfig,
    delta_path: str,
    table_name: str | None = None,
    *,
    overwrite: bool = True,
) -> str:
    """Publish one Delta table into the catalog and return its identifier.

    Raises ``FileNotFoundError`` when the Delta source does not exist. The
    table name defaults to the last segment of ``delta_path``.
    """
    source = config.resolve(delta_path)
    if not source.exists():
        raise FileNotFoundError(f"Delta source not found: {source}")
    arrow_table = DeltaTable(str(source)).to_pyarrow_dataset().to_table()
    name = table_name or default_table_name(delta_path)
    identifier = f"{config.lakehouse.namespace}.{name}"
    catalog = create_catalog(config)
    if catalog.table_exists(identifier):
        table = catalog.load_table(identifier)
        if overwrite:
            table.overwrite(arrow_table)
        else:
            table.append(arrow_table)
    else:
        table = catalog.create_table(identifier, schema=arrow_table.schema)
        table.append(arrow_table)
    return identifier


def publish_all(config: PlatformConfig) -> dict[str, str]:
    """Publish every configured Delta source that exists on disk.

    Missing sources are skipped; the mapping is table name to identifier.
    """
    storage = Storage(config)
    published: dict[str, str] = {}
    for delta_path in config.lakehouse.publish_tables:
        if not storage.exists(config.resolve(delta_path)):
            continue
        published[default_table_name(delta_path)] = publish_delta_table(config, delta_path)
    return published


def list_published_tables(config: PlatformConfig) -> list[str]:
    """Return the dotted identifiers registered in the configured namespace."""
    catalog = create_catalog(config)
    namespace = config.lakehouse.namespace
    return [".".join(identifier) for identifier in catalog.list_tables(namespace)]
