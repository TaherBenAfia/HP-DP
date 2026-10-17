"""Catalog construction: local SQLite namespace bootstrap and REST wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pyiceberg.catalog.rest import LegacyOAuth2AuthManager, RestCatalog
from pyiceberg.catalog.sql import SqlCatalog

from src.lakehouse.catalog import create_catalog, default_table_name
from src.platform.config import (
    LakehouseCatalogConfig,
    LakehouseConfig,
    PlatformConfig,
)


def test_local_catalog_creates_warehouse_and_namespace(
    lakehouse_config: PlatformConfig,
) -> None:
    catalog = create_catalog(lakehouse_config)
    assert isinstance(catalog, SqlCatalog)
    assert catalog.namespace_exists("statseo")
    assert lakehouse_config.resolve("wh").is_dir()


def test_relative_sqlite_uri_is_rebased_on_repo_root(tmp_path: Path) -> None:
    config = PlatformConfig(
        repo_root=tmp_path,
        lakehouse=LakehouseConfig(
            namespace="statseo",
            catalog=LakehouseCatalogConfig(
                type="local",
                uri="sqlite:///data/catalog.db",
                warehouse="wh",
            ),
        ),
    )
    catalog = create_catalog(config)
    assert isinstance(catalog, SqlCatalog)
    assert catalog.namespace_exists("statseo")
    assert (tmp_path / "data" / "catalog.db").exists()


def test_rest_catalog_builds_with_expected_kwargs(tmp_path: Path) -> None:
    """The REST client is wired from config without any network round-trip.

    PyIceberg fetches ``/v1/config`` and exchanges client credentials during
    construction, so both hooks are stubbed out for the offline test.
    """
    config = PlatformConfig(
        repo_root=tmp_path,
        lakehouse=LakehouseConfig(
            namespace="statseo",
            catalog=LakehouseCatalogConfig(
                type="rest",
                warehouse="wh_rest",
                rest_uri="https://polaris.example.com",
                rest_token="token-123",
                credential="cred-456",
            ),
        ),
    )
    with (
        patch.object(RestCatalog, "_fetch_config"),
        patch.object(LegacyOAuth2AuthManager, "_refresh_token"),
    ):
        catalog = create_catalog(config)
    assert isinstance(catalog, RestCatalog)
    assert catalog.uri == "https://polaris.example.com"
    assert catalog.properties["token"] == "token-123"
    assert catalog.properties["credential"] == "cred-456"
    assert catalog.properties["warehouse"] == "wh_rest"


def test_default_table_name_uses_last_segment() -> None:
    assert default_table_name("silver/sports_seo") == "sports_seo"
    assert default_table_name("gold/statistical_evidence/") == "statistical_evidence"
