"""Data product versioning.

Every published Gold table carries an explicit product identity so that a
consumer can always tell *which* version of *which* pipeline produced the
rows they are reading.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from src.platform.config import PlatformConfig, load_platform_config

PRODUCT_COLUMNS = [
    "product_id",
    "product_version",
    "generated_at",
    "source_dataset_version",
    "pipeline_version",
    "schema_version",
]


class DataProductVersion(BaseModel):
    """Identity stamp attached to a published Gold data product."""

    product_id: str
    product_version: str = "1.0.0"
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    source_dataset_version: str = "0.0.0"
    pipeline_version: str = "1.0.0"
    schema_version: str = "1.0.0"

    def as_columns(self, rows: int) -> dict[str, list[Any]]:
        """Materialise the stamp as column vectors of length ``rows``."""
        return {
            "product_id": [self.product_id] * rows,
            "product_version": [self.product_version] * rows,
            "generated_at": [self.generated_at] * rows,
            "source_dataset_version": [self.source_dataset_version] * rows,
            "pipeline_version": [self.pipeline_version] * rows,
            "schema_version": [self.schema_version] * rows,
        }


def stamp(frame: pl.DataFrame, version: DataProductVersion) -> pl.DataFrame:
    """Attach product-version columns to a frame (replacing any existing)."""
    existing = [c for c in PRODUCT_COLUMNS if c in frame.columns]
    if existing:
        frame = frame.drop(existing)
    return frame.with_columns(
        [pl.Series(name, values) for name, values in version.as_columns(frame.height).items()]
    )


def build_version(
    product_id: str,
    source_dataset_version: str,
    config: PlatformConfig | None = None,
    product_version: str | None = None,
) -> DataProductVersion:
    """Create a version stamp from platform configuration."""
    cfg = config or load_platform_config()
    return DataProductVersion(
        product_id=product_id,
        product_version=product_version or cfg.data_products.version,
        source_dataset_version=source_dataset_version,
        pipeline_version=cfg.platform.pipeline_version,
        schema_version=cfg.data_products.schema_version,
    )


def product_manifest(version: DataProductVersion, row_count: int, table: str) -> dict[str, Any]:
    """Sidecar manifest written next to a Gold table for ``share inspect``."""
    payload = version.model_dump()
    payload["table"] = table
    payload["row_count"] = row_count
    return payload
