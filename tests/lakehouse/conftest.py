"""Shared fixtures for the lakehouse test-suite; every artifact stays in ``tmp_path``."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.config import (
    LakehouseCatalogConfig,
    LakehouseConfig,
    PlatformConfig,
)


@pytest.fixture
def lakehouse_config(tmp_path: Path) -> PlatformConfig:
    """Local SQLite-backed lakehouse configuration rooted in a fresh directory."""
    return PlatformConfig(
        repo_root=tmp_path,
        lakehouse=LakehouseConfig(
            namespace="statseo",
            catalog=LakehouseCatalogConfig(
                type="local",
                uri=f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}",
                warehouse="wh",
            ),
            publish_tables=["silver/sports_seo"],
        ),
    )
