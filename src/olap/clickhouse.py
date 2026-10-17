"""Optional ClickHouse OLAP sink for published Gold data products.

ClickHouse is strictly opt-in: nothing here opens a socket unless
``config.olap.enabled`` is true and a URL is configured. The client factory is
an indirection so tests can exercise DDL generation and inserts without a
server.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import clickhouse_connect
import polars as pl

from src.platform.config import PlatformConfig, load_platform_config
from src.platform.storage import Storage
from src.query.engine import DEFAULT_VIEWS

_CLICKHOUSE_TYPE_BY_DTYPE: dict[Any, str] = {
    pl.Int8: "Int8",
    pl.Int16: "Int16",
    pl.Int32: "Int32",
    pl.Int64: "Int64",
    pl.UInt8: "UInt8",
    pl.UInt16: "UInt16",
    pl.UInt32: "UInt32",
    pl.UInt64: "UInt64",
    pl.Float32: "Float32",
    pl.Float64: "Float64",
    pl.Boolean: "Bool",
    pl.String: "String",
    pl.Categorical: "String",
    pl.Enum: "String",
    pl.Date: "Date",
    pl.Datetime: "DateTime64",
}


def _default_client_factory(**kwargs: Any) -> Any:
    """Create a real ``clickhouse_connect`` client."""
    return clickhouse_connect.get_client(**kwargs)


_client_factory: Callable[..., Any] = _default_client_factory


def is_enabled(config: PlatformConfig) -> bool:
    """True only when the sink is explicitly enabled and has a URL."""
    return bool(config.olap.enabled and config.olap.url)


def polars_to_clickhouse_type(dtype: pl.DataType) -> str:
    """Map a Polars dtype onto a ClickHouse column type (String fallback)."""
    base = dtype.base_type() if hasattr(dtype, "base_type") else dtype
    return _CLICKHOUSE_TYPE_BY_DTYPE.get(base, "String")


def _client_kwargs(config: PlatformConfig) -> dict[str, Any]:
    """Translate ``config.olap`` (and its URL) into client keyword arguments."""
    parsed = urlparse(config.olap.url)
    path_database = parsed.path.strip("/").split("/")[0] if parsed.path.strip("/") else ""
    database = path_database or config.olap.database
    return {
        "host": parsed.hostname,
        "port": parsed.port,
        "username": config.olap.username,
        "password": config.olap.password,
        "database": database,
        "secure": parsed.scheme == "https",
    }


def create_client(config: PlatformConfig | None = None) -> Any:
    """Create a ClickHouse client from ``config.olap``.

    Raises ``ValueError`` when the sink is disabled or has no URL.
    """
    config = config or load_platform_config()
    if not is_enabled(config):
        raise ValueError("ClickHouse OLAP sink is not enabled or has no URL")
    return _client_factory(**_client_kwargs(config))


def sync_table(
    config: PlatformConfig,
    table_name: str,
    frame: pl.DataFrame,
    *,
    replace: bool = True,
) -> str:
    """Create the ClickHouse table and insert ``frame``; return its full id."""
    client = create_client(config)
    database = config.olap.database
    full_name = f"{config.olap.table_prefix}{table_name}"
    qualified = f'"{database}"."{full_name}"'
    columns = ", ".join(
        f'"{name}" {polars_to_clickhouse_type(dtype)}'
        for name, dtype in frame.schema.items()
    )
    if replace:
        client.command(f"DROP TABLE IF EXISTS {qualified}")
    client.command(
        f"CREATE TABLE IF NOT EXISTS {qualified} ({columns}) "
        "ENGINE = MergeTree ORDER BY tuple()"
    )
    client.insert_df(
        table=full_name,
        df=frame.to_pandas(),
        database=database,
        column_names=list(frame.columns),
    )
    return f"{database}.{full_name}"


def sync_gold(
    config: PlatformConfig | None = None,
    tables: list[str] | None = None,
) -> dict[str, int]:
    """Sync each existing Gold table to ClickHouse; return row counts."""
    config = config or load_platform_config()
    storage = Storage(config)
    counts: dict[str, int] = {}
    for table in tables or DEFAULT_VIEWS:
        frame = storage.read(storage.gold_table(table))
        if frame is None:
            continue
        sync_table(config, table, frame)
        counts[table] = frame.height
    return counts


def query(config: PlatformConfig | None = None, sql: str = "") -> pl.DataFrame:
    """Run SQL against ClickHouse and return a Polars frame."""
    config = config or load_platform_config()
    client = create_client(config)
    return pl.from_arrow(client.query_arrow(sql))
