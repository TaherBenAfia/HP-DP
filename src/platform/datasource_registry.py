"""Datasource registry: loads, validates and fingerprints dataset contracts.

The registry references contracts instead of duplicating their schema, and it
is the single place that answers "what is this dataset, does it exist, and
which exact bytes produced the Bronze table?".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from src.ingestion.contracts import DatasourceContract, IngestionMetadata
from src.platform.config import PlatformConfig, load_platform_config
from src.platform.storage import Storage, schema_hash, sha256_file


@dataclass
class Check:
    """One validation check result."""

    name: str
    passed: bool
    message: str = ""
    severity: str = "error"

    def as_dict(self) -> dict[str, Any]:
        """Dictionary form used by the quality report."""
        return {
            "check_name": self.name,
            "passed": self.passed,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class ValidationReport:
    """Result of validating a datasource contract."""

    dataset_id: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when no error-severity check failed."""
        return all(check.passed for check in self.checks if check.severity == "error")

    @property
    def failures(self) -> list[Check]:
        """All failing checks."""
        return [check for check in self.checks if not check.passed]

    def as_dict(self) -> dict[str, Any]:
        """Dictionary form used by the CLI."""
        return {
            "dataset_id": self.dataset_id,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
        }

    def render(self) -> str:
        """Multi-line human-readable report."""
        lines = [f"{self.dataset_id}: {'VALID' if self.passed else 'INVALID'}"]
        for check in self.checks:
            mark = "ok  " if check.passed else "FAIL"
            suffix = f" - {check.message}" if check.message else ""
            lines.append(f"  [{mark}] {check.name}{suffix}")
        return "\n".join(lines)


class DatasourceRegistry:
    """Registry over ``configs/datasources/registry.json``."""

    def __init__(
        self,
        config: PlatformConfig | None = None,
        storage: Storage | None = None,
        registry_path: str | Path | None = None,
    ) -> None:
        self.config = config or load_platform_config()
        self.storage = storage or Storage(self.config)
        self.registry_path = Path(registry_path) if registry_path else self.config.resolve(
            self.config.paths.datasource_registry
        )
        self._entries: list[dict[str, Any]] | None = None
        self._contracts: dict[str, DatasourceContract] = {}

    # -- loading -----------------------------------------------------------
    def _load_entries(self) -> list[dict[str, Any]]:
        if self._entries is None:
            if not self.registry_path.exists():
                raise FileNotFoundError(f"datasource registry not found: {self.registry_path}")
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            entries = payload.get("datasources", [])
            if not isinstance(entries, list):
                raise ValueError("registry 'datasources' must be a list")
            self._entries = entries
        return self._entries

    def ids(self, enabled_only: bool = False) -> list[str]:
        """Registered dataset ids."""
        ids = [str(entry["id"]) for entry in self._load_entries()]
        if not enabled_only:
            return ids
        return [
            str(entry["id"])
            for entry in self._load_entries()
            if entry.get("enabled", True)
        ]

    def contract_path(self, dataset_id: str) -> Path:
        """Absolute path of the contract file for ``dataset_id``."""
        for entry in self._load_entries():
            if entry["id"] == dataset_id:
                return self.config.resolve(str(entry["contract"]))
        raise KeyError(f"unknown dataset id: {dataset_id}")

    def get(self, dataset_id: str) -> DatasourceContract:
        """Load and validate a single contract (cached)."""
        if dataset_id not in self._contracts:
            path = self.contract_path(dataset_id)
            if not path.exists():
                raise FileNotFoundError(f"datasource contract not found: {path}")
            contract = DatasourceContract.model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )
            if contract.dataset_id != dataset_id:
                raise ValueError(
                    f"contract id mismatch: registry says {dataset_id!r}, "
                    f"contract says {contract.dataset_id!r}"
                )
            self._contracts[dataset_id] = contract
        return self._contracts[dataset_id]

    def all(self, enabled_only: bool = False) -> list[DatasourceContract]:
        """All registered contracts."""
        return [self.get(dataset_id) for dataset_id in self.ids(enabled_only)]

    # -- physical source ---------------------------------------------------
    def resolve_path(self, contract: DatasourceContract) -> Path:
        """Resolve the physical source file for a contract."""
        return self.config.resolve(contract.path)

    def exists(self, contract: DatasourceContract) -> bool:
        """True when the physical source file exists."""
        return self.resolve_path(contract).exists()

    def fingerprint(self, contract: DatasourceContract) -> dict[str, Any]:
        """Checksum and file-level facts for the physical source file."""
        path = self.resolve_path(contract)
        if not path.exists():
            raise FileNotFoundError(f"source file not found: {path}")
        stat = path.stat()
        return {
            "source_path": str(path.relative_to(self.config.repo_root))
            if path.is_relative_to(self.config.repo_root)
            else str(path),
            "source_checksum": sha256_file(path),
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(
                timespec="seconds"
            ),
        }

    def checksum(self, contract: DatasourceContract) -> str:
        """SHA-256 of the physical source file."""
        return str(self.fingerprint(contract)["source_checksum"])

    # -- schema metadata ---------------------------------------------------
    def schema_metadata(self, contract: DatasourceContract) -> list[dict[str, Any]]:
        """Full attribute inventory as plain dictionaries."""
        return [attribute.model_dump() for attribute in contract.attributes]

    def columns(self, contract: DatasourceContract, source_only: bool = True) -> list[str]:
        """Ordered column names for a contract."""
        attributes = contract.source_attributes if source_only else contract.attributes
        return [attribute.name for attribute in attributes]

    # -- validation --------------------------------------------------------
    def validate(self, dataset_id: str) -> ValidationReport:
        """Validate contract structure, source existence and readability."""
        report = ValidationReport(dataset_id=dataset_id)
        try:
            contract = self.get(dataset_id)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.checks.append(Check("contract_loadable", False, str(exc)))
            return report

        report.checks.append(Check("contract_loadable", True, contract.dataset_name))
        report.checks.append(
            Check(
                "attributes_present",
                bool(contract.attributes),
                f"{len(contract.attributes)} attributes declared",
            )
        )
        report.checks.append(
            Check(
                "primary_key_declared",
                bool(contract.primary_key),
                ",".join(contract.primary_key) or "no primary key",
            )
        )
        report.checks.append(
            Check(
                "origin_classes_declared",
                bool(contract.origin_summary()),
                json.dumps(contract.origin_summary(), sort_keys=True),
            )
        )

        path = self.resolve_path(contract)
        report.checks.append(
            Check("source_file_exists", path.exists(), str(path) if path.exists() else str(path))
        )
        if not path.exists():
            return report

        fingerprint = self.fingerprint(contract)
        report.checks.append(
            Check("source_checksum", True, fingerprint["source_checksum"][:16], "info")
        )
        if contract.format == "csv":
            try:
                header = (
                    path.open("r", encoding="utf-8").readline().strip().split(",")
                )
                missing = [c for c in contract.required_columns if c not in header]
                extra = [c for c in header if c not in contract.source_columns]
                report.checks.append(
                    Check(
                        "required_columns_present",
                        not missing,
                        f"missing={missing}" if missing else f"{len(header)} columns read",
                    )
                )
                report.checks.append(
                    Check(
                        "extra_columns_allowed",
                        contract.validation.allow_extra_columns or not extra,
                        f"extra={extra}" if extra else "no extra columns",
                        "error" if not contract.validation.allow_extra_columns else "info",
                    )
                )
                report.checks.append(
                    Check("schema_hash", True, schema_hash(header), "info")
                )
            except Exception as exc:  # noqa: BLE001
                report.checks.append(Check("source_readable", False, str(exc)))
        return report

    # -- ingestion metadata ------------------------------------------------
    def record_ingestion(self, metadata: IngestionMetadata) -> Path:
        """Append an ingestion record to ``bronze/_metadata/ingestion_log.delta``.

        Re-recording the same ``(dataset_id, run_id)`` replaces the previous
        entry, so the log stays append-only in spirit and idempotent in fact.
        """
        table = self.storage.bronze_metadata()
        frame = pl.DataFrame([metadata.to_row()])
        existing = self.storage.read(table)
        if existing is not None and existing.height:
            keep = existing.filter(
                ~(
                    (pl.col("dataset_id") == metadata.dataset_id)
                    & (pl.col("run_id") == metadata.run_id)
                )
            )
            frame = pl.concat([keep, frame], how="diagonal_relaxed")
        self.storage.write(table, frame, mode="overwrite")
        return table

    def ingestion_log(self) -> Any:
        """All ingestion records, newest first."""
        frame = self.storage.read(self.storage.bronze_metadata())
        if frame is None or not frame.height:
            return frame
        return frame.sort("ingested_at", descending=True)

    def last_successful_run(self, dataset_id: str) -> dict[str, Any] | None:
        """Most recent successful ingestion for a dataset (idempotency anchor)."""
        frame = self.ingestion_log()
        if frame is None or not frame.height:
            return None
        rows = frame.filter(
            (pl.col("dataset_id") == dataset_id) & (pl.col("status") == "success")
        ).to_dicts()
        if not rows:
            return None
        return sorted(rows, key=lambda row: row["ingested_at"])[-1]
