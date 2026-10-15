"""Format readers for the ingestion layer.

Readers return the source *as it is*: no normalisation, no casting, no row
dropping. Everything that changes meaning happens in Silver.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from src.ingestion.contracts import DatasourceContract
from src.platform.config import PlatformConfig, load_platform_config


class ReadError(Exception):
    """Raised when a physical source cannot be read."""


def read_source(
    contract: DatasourceContract,
    path: Path,
    config: PlatformConfig | None = None,
) -> pl.DataFrame:
    """Read a source file into a Polars dataframe using contract settings."""
    cfg = config or load_platform_config()
    if not path.exists():
        raise ReadError(f"source file not found: {path}")

    try:
        if contract.format == "csv":
            options = cfg.ingestion.csv
            return pl.read_csv(
                path,
                separator=options.delimiter,
                encoding=options.encoding,
                null_values=options.null_values,
                infer_schema_length=10_000,
                try_parse_dates=False,
                ignore_errors=False,
                raise_if_empty=True,
            )
        if contract.format == "parquet":
            return pl.read_parquet(path)
        if contract.format == "json":
            return pl.read_ndjson(path)
    except ReadError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as a typed error
        raise ReadError(f"failed to read {path}: {exc}") from exc
    raise ReadError(f"unsupported format: {contract.format}")


def preview(contract: DatasourceContract, path: Path, rows: int = 5) -> pl.DataFrame:
    """Read a small slice of the source for ``datasource inspect``."""
    frame = read_source(contract, path)
    return frame.head(rows)
