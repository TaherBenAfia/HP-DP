"""Evidence-native lakehouse: publish local Delta tables into Iceberg.

The package exposes a tiny surface: build a catalog, publish one or all
configured Delta sources, list what was published, and read the results back
through Polars or DuckDB without touching the network.
"""

from __future__ import annotations

from src.lakehouse.catalog import create_catalog
from src.lakehouse.readers import duckdb_views, read_table_polars
from src.lakehouse.tables import (
    list_published_tables,
    publish_all,
    publish_delta_table,
)

__all__ = [
    "create_catalog",
    "duckdb_views",
    "list_published_tables",
    "publish_all",
    "publish_delta_table",
    "read_table_polars",
]
