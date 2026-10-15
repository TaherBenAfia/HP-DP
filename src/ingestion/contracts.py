"""Data source contracts.

A contract is the *only* place that describes an external dataset: its
location, its primary key, its full attribute inventory, and how strictly the
ingestion engine must validate it. Ingestion code never hard-codes a schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field, model_validator

RunStatus = Literal["started", "success", "failed", "skipped"]


class FieldOrigin(StrEnum):
    """Where a field actually comes from.

    Derived fields must never be presented as raw source measurements, which
    is why origin is part of the contract rather than documentation.
    """

    RAW_SOURCE = "RAW_SOURCE"
    NORMALIZED = "NORMALIZED"
    DERIVED_FEATURE = "DERIVED_FEATURE"
    ANALYTICAL_OUTPUT = "ANALYTICAL_OUTPUT"
    STATISTICAL_OUTPUT = "STATISTICAL_OUTPUT"
    DECISION_OUTPUT = "DECISION_OUTPUT"


class Attribute(BaseModel):
    """One column of a dataset."""

    name: str
    dtype: str
    nullable: bool = True
    origin: FieldOrigin = FieldOrigin.RAW_SOURCE
    semantic_type: str = "observation"
    present_in_source: bool = True
    available: bool = True
    description: str = ""


class ValidationRules(BaseModel):
    """How strictly the source file must match the contract."""

    allow_extra_columns: bool = True
    allow_missing_columns: bool = False
    primary_key_must_be_unique: bool = True


class SourceLineage(BaseModel):
    """Provenance notes supplied with the contract."""

    originating_project: str = ""
    produced_by: str = ""
    observation_type: str = "observational"
    notes: list[str] = Field(default_factory=list)


class DatasourceContract(BaseModel):
    """A complete, machine-readable description of one dataset."""

    dataset_id: str
    dataset_name: str
    domain: str
    version: str = "1.0.0"
    format: Literal["csv", "parquet", "json"] = "csv"
    path: str
    primary_key: list[str] = Field(default_factory=list)
    partition_columns: list[str] = Field(default_factory=list)
    classification: str = "internal"
    description: str = ""
    source_type: str = ""
    refresh_frequency: str = "manual"
    schema_version: str = "1.0.0"
    row_count_expected: int | None = None
    column_count_expected: int | None = None
    validation: ValidationRules = Field(default_factory=ValidationRules)
    lineage: SourceLineage = Field(default_factory=SourceLineage)
    attributes: list[Attribute] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_attribute_names(self) -> DatasourceContract:
        names = [attribute.name for attribute in self.attributes]
        if len(names) != len(set(names)):
            duplicates = sorted({name for name in names if names.count(name) > 1})
            raise ValueError(f"duplicate attribute names in contract: {duplicates}")
        if len(self.primary_key) > len(set(self.primary_key)):
            raise ValueError("primary_key contains duplicates")
        return self

    # -- derived views -----------------------------------------------------
    @property
    def source_attributes(self) -> list[Attribute]:
        """Attributes expected to be physically present in the source file."""
        return [attribute for attribute in self.attributes if attribute.present_in_source]

    @property
    def platform_attributes(self) -> list[Attribute]:
        """Attributes the platform computes after ingestion."""
        return [attribute for attribute in self.attributes if not attribute.present_in_source]

    @property
    def required_columns(self) -> list[str]:
        """Columns the source file must contain."""
        if self.validation.allow_missing_columns:
            return []
        return [attribute.name for attribute in self.source_attributes]

    @property
    def source_columns(self) -> list[str]:
        """Ordered column list expected in the source file."""
        return [attribute.name for attribute in self.source_attributes]

    def attribute(self, name: str) -> Attribute | None:
        """Look up a single attribute by name."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute
        return None

    def polars_schema(self, source_only: bool = True) -> dict[str, pl.DataType]:
        """Expected Polars schema (as a validation target, not a cast list)."""
        attributes = self.source_attributes if source_only else self.attributes
        return {attribute.name: dtype_from_contract(attribute.dtype) for attribute in attributes}

    def origin_summary(self) -> dict[str, int]:
        """Count of attributes per field-origin class."""
        summary: dict[str, int] = {}
        for attribute in self.attributes:
            summary[attribute.origin.value] = summary.get(attribute.origin.value, 0) + 1
        return summary


class IngestionMetadata(BaseModel):
    """Metadata stamped on every ingestion run and its Bronze output."""

    run_id: str
    dataset_id: str
    dataset_version: str
    schema_version: str = "1.0.0"
    source_path: str
    source_checksum: str
    ingested_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )
    row_count: int = 0
    column_count: int = 0
    schema_hash: str = ""
    pipeline_version: str = "1.0.0"
    status: RunStatus = "started"
    error: str | None = None
    fingerprint: dict[str, Any] = Field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        """Flatten for the ingestion log Delta table."""
        payload = self.model_dump()
        import json

        payload["fingerprint"] = json.dumps(payload["fingerprint"], sort_keys=True)
        return payload


_CONTRACT_TO_POLARS: dict[str, pl.DataType] = {
    "int64": pl.Int64,
    "int32": pl.Int32,
    "int16": pl.Int16,
    "int8": pl.Int8,
    "uint64": pl.UInt64,
    "float64": pl.Float64,
    "float32": pl.Float32,
    "bool": pl.Boolean,
    "boolean": pl.Boolean,
    "object": pl.String,
    "string": pl.String,
    "str": pl.String,
    "datetime": pl.Datetime,
    "date": pl.Date,
}  # type: ignore[dict-item]


def dtype_from_contract(dtype: str) -> pl.DataType:
    """Map a contract dtype string to a Polars dtype."""
    return _CONTRACT_TO_POLARS.get(dtype.lower(), pl.String)


def dtype_family(dtype: pl.DataType) -> str:
    """Reduce a Polars dtype to a coarse family used by type validation."""
    if dtype in (pl.String, pl.Categorical):
        return "string"
    if isinstance(dtype, pl.Enum) or str(dtype).startswith("Enum"):
        return "string"
    if dtype == pl.Boolean:
        return "boolean"
    if dtype.is_temporal():
        return "temporal"
    if dtype.is_integer():
        return "integer"
    if dtype.is_float():
        return "float"
    return "other"


def contract_family(dtype: str) -> str:
    """Reduce a contract dtype string to a coarse family."""
    mapped = dtype_from_contract(dtype)
    if mapped == pl.String:
        return "string"
    return dtype_family(mapped)
