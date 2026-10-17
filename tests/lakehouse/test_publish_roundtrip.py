"""Delta -> Iceberg -> Polars round-trip, including overwrite semantics."""

from __future__ import annotations

import polars as pl
import pytest

from src.lakehouse import (
    list_published_tables,
    publish_all,
    publish_delta_table,
    read_table_polars,
)
from src.platform.config import PlatformConfig
from src.platform.storage import Storage


def test_publish_roundtrip_and_overwrite(lakehouse_config: PlatformConfig) -> None:
    config = lakehouse_config
    original = pl.DataFrame({"rank": [3, 1, 2], "domain": ["a", "b", "c"]})
    Storage(config).write(config.resolve("silver/sports_seo"), original)

    identifier = publish_delta_table(config, "silver/sports_seo")
    assert identifier == "statseo.sports_seo"
    assert identifier in list_published_tables(config)

    published = read_table_polars(config, "sports_seo").sort("rank")
    assert published.rows() == original.sort("rank").rows()

    assert publish_all(config) == {"sports_seo": "statseo.sports_seo"}
    assert read_table_polars(config, "sports_seo").height == 3

    publish_delta_table(config, "silver/sports_seo", overwrite=True)
    assert read_table_polars(config, "sports_seo").height == 3


def test_publish_appends_when_overwrite_is_disabled(
    lakehouse_config: PlatformConfig,
) -> None:
    config = lakehouse_config
    Storage(config).write(config.resolve("silver/sports_seo"), pl.DataFrame({"n": [1]}))
    publish_delta_table(config, "silver/sports_seo")
    publish_delta_table(config, "silver/sports_seo", overwrite=False)
    assert read_table_polars(config, "sports_seo").height == 2


def test_publish_all_skips_missing_sources(lakehouse_config: PlatformConfig) -> None:
    assert publish_all(lakehouse_config) == {}


def test_publish_missing_source_raises(lakehouse_config: PlatformConfig) -> None:
    with pytest.raises(FileNotFoundError):
        publish_delta_table(lakehouse_config, "gold/statistical_evidence")
