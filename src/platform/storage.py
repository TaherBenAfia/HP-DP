"""Local filesystem storage helpers for Delta / Parquet tables.

The platform is local-first: every table lives under ``data/`` and is written
through ``delta-rs`` (no Spark, no cloud storage). MinIO can be layered on
later without changing this interface because callers only ever deal in
table paths and Arrow-compatible dataframes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import polars as pl
from deltalake import DeltaTable, write_deltalake

from src.platform.config import PlatformConfig, load_platform_config

WriteMode = Literal["error", "append", "overwrite", "ignore"]


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return the hex SHA-256 digest of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def schema_hash(columns: list[str]) -> str:
    """Return a stable short hash of an ordered column list."""
    return hashlib.sha256("|".join(columns).encode("utf-8")).hexdigest()[:16]


class Storage:
    """Resolves platform table locations and performs Delta I/O."""

    def __init__(self, config: PlatformConfig | None = None) -> None:
        self.config = config or load_platform_config()

    # -- locations ---------------------------------------------------------
    @property
    def root(self) -> Path:
        """Repository root."""
        return self.config.repo_root

    def path(self, relative: str | Path) -> Path:
        """Resolve a repo-relative path."""
        return self.config.resolve(relative)

    def bronze_table(self, domain: str) -> Path:
        """Immutable raw table location for a domain."""
        return self.path(f"{self.config.paths.bronze_dir}/{domain}")

    def bronze_metadata(self) -> Path:
        """Ingestion log location (append-only Delta table)."""
        return self.path(self.config.paths.bronze_metadata_dir)

    def bronze_manifest_dir(self, dataset_id: str) -> Path:
        """Directory holding per-run ingestion manifests."""
        return self.path(f"{self.config.paths.bronze_manifest_dir}/{dataset_id}")

    def silver_table(self, domain: str) -> Path:
        """Validated / normalised table location for a domain."""
        return self.path(f"{self.config.paths.silver_dir}/{domain}")

    def quarantine_table(self, domain: str) -> Path:
        """Quarantine table location for a domain."""
        return self.path(f"{self.config.paths.quarantine_dir}/{domain}")

    def gold_table(self, product: str) -> Path:
        """Domain-neutral Gold data product location."""
        return self.path(f"{self.config.paths.gold_dir}/{product}")

    def gold_domain_table(self, domain: str, product: str) -> Path:
        """Domain-specific Gold data product location."""
        return self.path(f"{self.config.paths.gold_dir}/{domain}/{product}")

    def duckdb_path(self) -> Path:
        """Path of the local DuckDB analytics database."""
        return self.path(self.config.paths.duckdb_path)

    # -- Delta I/O ---------------------------------------------------------
    def exists(self, table: Path) -> bool:
        """True when a Delta table exists at ``table``."""
        return (table / "_delta_log").exists()

    def write(
        self,
        table: Path,
        data: pl.DataFrame | Any,
        mode: WriteMode = "overwrite",
        schema_mode: Literal["merge", "overwrite"] | None = None,
        partition_by: list[str] | None = None,
    ) -> Path:
        """Write ``data`` to a Delta table, creating parent directories."""
        table.parent.mkdir(parents=True, exist_ok=True)
        table.mkdir(parents=True, exist_ok=True)
        if not isinstance(data, pl.DataFrame):
            data = pl.DataFrame(data)
        # All-null columns infer to the Null dtype, which Arrow cannot write.
        null_columns = [name for name, dtype in data.schema.items() if dtype == pl.Null]
        if null_columns:
            data = data.with_columns(
                [pl.col(name).cast(pl.String) for name in null_columns]
            )
        write_deltalake(
            table,
            data,
            mode=mode,
            schema_mode=schema_mode,
            partition_by=partition_by,
        )
        return table

    def append(self, table: Path, data: pl.DataFrame) -> Path:
        """Append rows to an existing Delta table (creating it if needed)."""
        mode: WriteMode = "append" if self.exists(table) else "error"
        return self.write(table, data, mode=mode)

    def read(self, table: Path) -> pl.DataFrame | None:
        """Read a Delta table into a Polars dataframe, or ``None`` if absent."""
        if not self.exists(table):
            return None
        delta = DeltaTable(str(table))
        dataset = delta.to_pyarrow_dataset()
        return pl.from_arrow(dataset.to_table())

    def read_batch(self, table: Path, columns: list[str] | None = None) -> pl.DataFrame | None:
        """Read a Delta table, optionally projecting a column subset."""
        if not self.exists(table):
            return None
        delta = DeltaTable(str(table))
        dataset = delta.to_pyarrow_dataset()
        return pl.from_arrow(dataset.to_table(columns=columns))

    def history(self, table: Path, limit: int = 10) -> list[dict[str, Any]]:
        """Return Delta transaction history entries (empty when unversioned)."""
        if not self.exists(table):
            return []
        return DeltaTable(str(table)).history(limit)
