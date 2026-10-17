"""Iceberg catalog construction for the lakehouse layer.

Local deployments use the SQLite-backed ``SqlCatalog``; REST deployments such
as Apache Polaris use the REST catalog client. Relative SQLite URIs are
rebased on the repository root so catalogs resolve identically regardless of
the process working directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyiceberg.catalog.rest import RestCatalog as RESTCatalog
from pyiceberg.catalog.sql import SqlCatalog

from src.platform.config import PlatformConfig

_SQLITE_PREFIX = "sqlite:///"


def _sqlite_uri(config: PlatformConfig, uri: str) -> str:
    """Rebase a relative ``sqlite:///`` URI onto the repository root."""
    if not uri.startswith(_SQLITE_PREFIX):
        return uri
    target = Path(uri[len(_SQLITE_PREFIX) :])
    if not target.is_absolute():
        target = config.resolve(target)
    return f"{_SQLITE_PREFIX}{target.as_posix()}"


def create_catalog(config: PlatformConfig) -> Any:
    """Build the configured Iceberg catalog and ensure its namespace exists."""
    settings = config.lakehouse.catalog
    name = config.lakehouse.namespace
    if settings.type == "rest":
        return RESTCatalog(
            name,
            uri=settings.rest_uri,
            token=settings.rest_token,
            credential=settings.credential,
            warehouse=settings.warehouse,
        )
    uri = _sqlite_uri(config, settings.uri)
    if uri.startswith(_SQLITE_PREFIX):
        Path(uri[len(_SQLITE_PREFIX) :]).parent.mkdir(parents=True, exist_ok=True)
    warehouse = config.resolve(settings.warehouse)
    warehouse.mkdir(parents=True, exist_ok=True)
    catalog = SqlCatalog(name, uri=uri, warehouse=str(warehouse))
    catalog.create_namespace_if_not_exists(config.lakehouse.namespace)
    return catalog


def default_table_name(delta_path: str) -> str:
    """Return the last path segment of a repo-relative Delta path."""
    return Path(delta_path).name
