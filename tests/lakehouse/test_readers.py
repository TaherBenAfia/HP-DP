"""DuckDB views over published Parquet data files, fully offline."""

from __future__ import annotations

import duckdb
import polars as pl

from src.lakehouse import duckdb_views, publish_delta_table, read_table_polars
from src.platform.config import PlatformConfig
from src.platform.storage import Storage


def test_duckdb_views_expose_published_rows(lakehouse_config: PlatformConfig) -> None:
    config = lakehouse_config
    Storage(config).write(config.resolve("silver/sports_seo"), pl.DataFrame({"n": [1, 2, 3]}))
    publish_delta_table(config, "silver/sports_seo")

    connection = duckdb.connect()
    try:
        views = duckdb_views(config, connection)
        assert views == ["sports_seo"]
        count = connection.execute('SELECT count(*) FROM "sports_seo"').fetchone()
        assert count is not None and count[0] == 3
        values = connection.execute('SELECT sum(n) FROM "sports_seo"').fetchone()
        assert values is not None and values[0] == 6
    finally:
        connection.close()


def test_duckdb_views_skip_unpublished_sources(
    lakehouse_config: PlatformConfig,
) -> None:
    connection = duckdb.connect()
    try:
        assert duckdb_views(lakehouse_config, connection) == []
    finally:
        connection.close()


def test_read_table_polars_matches_delta_source(
    lakehouse_config: PlatformConfig,
) -> None:
    config = lakehouse_config
    source = pl.DataFrame({"page_id": ["a", "b"], "score": [0.5, 0.75]})
    Storage(config).write(config.resolve("silver/sports_seo"), source)
    publish_delta_table(config, "silver/sports_seo")
    frame = read_table_polars(config, "sports_seo").sort("page_id")
    assert frame.rows() == source.sort("page_id").rows()
