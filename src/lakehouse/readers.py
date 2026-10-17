"""Read published Iceberg tables back into Polars and DuckDB.

DuckDB access is deliberately offline: instead of installing the Iceberg
extension (which downloads from the network), views are built directly over
the Parquet data files that PyIceberg wrote into the warehouse.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import duckdb
import polars as pl

from src.lakehouse.catalog import create_catalog
from src.lakehouse.tables import list_published_tables
from src.platform.config import PlatformConfig


def read_table_polars(config: PlatformConfig, table_name: str) -> pl.DataFrame:
    """Scan a published Iceberg table into a Polars dataframe."""
    catalog = create_catalog(config)
    table = catalog.load_table(f"{config.lakehouse.namespace}.{table_name}")
    return cast(pl.DataFrame, pl.from_arrow(table.scan().to_arrow()))


def duckdb_views(
    config: PlatformConfig, connection: duckdb.DuckDBPyConnection
) -> list[str]:
    """Register one DuckDB view per published table over its Parquet files.

    Returns the view names actually created; tables without on-disk data
    files are skipped, which also covers REST catalogs whose warehouse is not
    reachable from this process.
    """
    views: list[str] = []
    for table_name in _published_names(config):
        data_glob = _parquet_glob(config, table_name)
        if data_glob is None:
            continue
        escaped = data_glob.replace("'", "''")
        connection.execute(
            f'CREATE OR REPLACE VIEW "{table_name}" AS '
            f"SELECT * FROM read_parquet('{escaped}')"
        )
        views.append(table_name)
    return views


def _published_names(config: PlatformConfig) -> list[str]:
    """Table names to expose: catalog entries locally, warehouse folders on REST."""
    if config.lakehouse.catalog.type == "rest":
        return _warehouse_table_names(config)
    identifiers = list_published_tables(config)
    return [identifier.rsplit(".", 1)[-1] for identifier in identifiers]


def _namespace_dirs(config: PlatformConfig) -> list[Path]:
    """Existing warehouse directories that may hold the configured namespace."""
    warehouse = config.resolve(config.lakehouse.catalog.warehouse)
    namespace = config.lakehouse.namespace
    candidates = [warehouse / namespace, warehouse / f"{namespace}.db"]
    return [candidate for candidate in candidates if candidate.is_dir()]


def _warehouse_table_names(config: PlatformConfig) -> list[str]:
    """Discover published table folders directly inside the warehouse."""
    names: list[str] = []
    for namespace_dir in _namespace_dirs(config):
        for child in sorted(namespace_dir.iterdir()):
            if child.is_dir() and (child / "data").is_dir():
                names.append(child.name)
    return names


def _parquet_glob(config: PlatformConfig, table_name: str) -> str | None:
    """Return a forward-slash Parquet glob for a table, or ``None`` if absent."""
    for namespace_dir in _namespace_dirs(config):
        data_dir = namespace_dir / table_name / "data"
        if data_dir.is_dir() and next(data_dir.glob("*.parquet"), None) is not None:
            return f"{data_dir.as_posix()}/*.parquet"
    return None
