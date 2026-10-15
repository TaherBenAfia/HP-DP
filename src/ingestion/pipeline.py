"""Bronze ingestion pipeline.

Steps (identical for every dataset, regardless of domain):

    contract -> source exists -> fingerprint -> read -> validate schema
    -> stamp ingestion metadata -> write Bronze Delta -> manifest -> log
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from src.ingestion.contracts import DatasourceContract, IngestionMetadata
from src.ingestion.fingerprint import fingerprint_file, new_run_id
from src.ingestion.reader import read_source
from src.ingestion.validator import failing_checks, validate_schema
from src.platform.config import PlatformConfig, load_platform_config
from src.platform.datasource_registry import Check, DatasourceRegistry
from src.platform.lineage import LineageRecord, LineageStore
from src.platform.storage import Storage, schema_hash

# Columns stamped onto every Bronze row so a row can always be traced back.
METADATA_COLUMNS = [
    "_dataset_id",
    "_dataset_version",
    "_run_id",
    "_source_checksum",
    "_ingested_at",
    "_pipeline_version",
]


class IngestionError(Exception):
    """Raised when a source cannot be ingested."""

    def __init__(self, message: str, checks: list[Check] | None = None) -> None:
        super().__init__(message)
        self.checks = checks or []


@dataclass
class IngestionResult:
    """Outcome of one ingestion run."""

    run_id: str
    dataset_id: str
    status: str
    bronze_table: str = ""
    row_count: int = 0
    column_count: int = 0
    source_checksum: str = ""
    source_path: str = ""
    skipped: bool = False
    reason: str = ""
    manifest_path: str = ""
    checks: list[Check] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Dictionary form for the CLI."""
        return {
            "run_id": self.run_id,
            "dataset_id": self.dataset_id,
            "status": self.status,
            "bronze_table": self.bronze_table,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "source_checksum": self.source_checksum,
            "source_path": self.source_path,
            "skipped": self.skipped,
            "reason": self.reason,
            "manifest_path": self.manifest_path,
            "checks": [check.as_dict() for check in self.checks],
        }


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_relative_to(root) else str(path)


def ingest(
    dataset_id: str,
    config: PlatformConfig | None = None,
    registry: DatasourceRegistry | None = None,
    force: bool = False,
) -> IngestionResult:
    """Ingest one dataset into Bronze.

    The run is idempotent: if the source checksum is unchanged and the Bronze
    table already exists, the previous run is re-used instead of appending
    duplicate rows.
    """
    cfg = config or load_platform_config()
    store = Storage(cfg)
    registry = registry or DatasourceRegistry(cfg, store)
    contract = registry.get(dataset_id)

    # 1. contract + source validation -------------------------------------
    report = registry.validate(dataset_id)
    if not report.passed:
        raise IngestionError(
            f"datasource validation failed for {dataset_id}", report.failures
        )

    source_path = registry.resolve_path(contract)
    fingerprint = fingerprint_file(source_path, cfg.repo_root)
    run_id = new_run_id(dataset_id)

    # 2. idempotency -------------------------------------------------------
    bronze = store.bronze_table(contract.domain)
    previous = registry.last_successful_run(dataset_id)
    if (
        cfg.ingestion.idempotent
        and not force
        and previous is not None
        and previous.get("source_checksum") == fingerprint.source_checksum
        and store.exists(bronze)
    ):
        result = IngestionResult(
            run_id=str(previous["run_id"]),
            dataset_id=dataset_id,
            status="skipped",
            bronze_table=_relative(bronze, cfg.repo_root),
            row_count=int(previous.get("row_count", 0)),
            column_count=int(previous.get("column_count", 0)),
            source_checksum=fingerprint.source_checksum,
            source_path=fingerprint.source_path,
            skipped=True,
            reason="source checksum unchanged since the previous successful run",
            checks=report.checks,
        )
        _write_manifest(cfg, contract, result, fingerprint, [])
        return result

    # 3. read + schema validation -----------------------------------------
    try:
        frame = read_source(contract, source_path, cfg)
    except Exception as exc:  # noqa: BLE001
        raise IngestionError(f"failed to read source: {exc}", report.checks) from exc

    checks = report.checks + validate_schema(frame, contract)
    blocking = failing_checks(checks)
    if blocking:
        raise IngestionError(
            f"schema validation failed for {dataset_id}: "
            + "; ".join(f"{c.name}: {c.message}" for c in blocking),
            checks,
        )

    # 4. stamp ingestion metadata -----------------------------------------
    ingested_at = fingerprint.modified_at
    stamped = frame.with_columns(
        [
            pl.lit(dataset_id).alias("_dataset_id"),
            pl.lit(contract.version).alias("_dataset_version"),
            pl.lit(run_id).alias("_run_id"),
            pl.lit(fingerprint.source_checksum).alias("_source_checksum"),
            pl.lit(ingested_at).alias("_ingested_at"),
            pl.lit(cfg.platform.pipeline_version).alias("_pipeline_version"),
        ]
    )

    # 5. write Bronze (append-only; forced re-ingest replaces this dataset) --
    if force and store.exists(bronze):
        existing = store.read(bronze)
        if existing is not None and existing.height:
            stamped = pl.concat(
                [existing.filter(pl.col("_dataset_id") != dataset_id), stamped],
                how="diagonal_relaxed",
            )
        store.write(bronze, stamped, mode="overwrite")
    else:
        store.write(bronze, stamped, mode="append" if store.exists(bronze) else "error")

    # 6. metadata + manifest + lineage -------------------------------------
    metadata = IngestionMetadata(
        run_id=run_id,
        dataset_id=dataset_id,
        dataset_version=contract.version,
        schema_version=contract.schema_version,
        source_path=fingerprint.source_path,
        source_checksum=fingerprint.source_checksum,
        ingested_at=ingested_at,
        row_count=frame.height,
        column_count=frame.width,
        schema_hash=schema_hash(frame.columns),
        pipeline_version=cfg.platform.pipeline_version,
        status="success",
        fingerprint=fingerprint.as_dict(),
    )
    registry.record_ingestion(metadata)

    result = IngestionResult(
        run_id=run_id,
        dataset_id=dataset_id,
        status="success",
        bronze_table=_relative(bronze, cfg.repo_root),
        row_count=frame.height,
        column_count=frame.width,
        source_checksum=fingerprint.source_checksum,
        source_path=fingerprint.source_path,
        checks=checks,
    )
    manifest_path = _write_manifest(cfg, contract, result, fingerprint, checks)
    result.manifest_path = manifest_path

    LineageStore(cfg, store).record(
        LineageRecord(
            table=f"bronze/{contract.domain}",
            layer="bronze",
            run_id=run_id,
            dataset_id=dataset_id,
            dataset_version=contract.version,
            source_path=fingerprint.source_path,
            source_checksum=fingerprint.source_checksum,
            parent_tables=[fingerprint.source_path],
            row_count=frame.height,
        )
    )
    return result


def _write_manifest(
    cfg: PlatformConfig,
    contract: DatasourceContract,
    result: IngestionResult,
    fingerprint: Any,
    checks: list[Check],
) -> str:
    """Persist a JSON run manifest under ``bronze/_manifests``."""
    directory = cfg.resolve(f"{cfg.paths.bronze_manifest_dir}/{contract.dataset_id}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.run_id}.json"
    payload = {
        "run": result.as_dict(),
        "fingerprint": fingerprint.as_dict(),
        "contract": {
            "dataset_id": contract.dataset_id,
            "version": contract.version,
            "schema_version": contract.schema_version,
            "primary_key": contract.primary_key,
            "origin_summary": contract.origin_summary(),
        },
        "checks": [check.as_dict() for check in checks],
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(path.relative_to(cfg.repo_root)) if path.is_relative_to(cfg.repo_root) else str(path)
