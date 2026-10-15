"""Schema validation for the ingestion layer.

Checks here are *schema level*: columns, dtypes, primary-key presence. Row
level rules (range checks, malformed values, duplicates) belong to the Silver
quality layer.
"""

from __future__ import annotations

import polars as pl

from src.ingestion.contracts import (
    Attribute,
    DatasourceContract,
    contract_family,
    dtype_family,
)
from src.platform.datasource_registry import Check


def validate_columns(frame: pl.DataFrame, contract: DatasourceContract) -> list[Check]:
    """Check required columns, extra columns and primary-key columns."""
    checks: list[Check] = []
    present = set(frame.columns)
    required = contract.required_columns
    missing = [column for column in required if column not in present]
    checks.append(
        Check(
            "required_columns_present",
            not missing,
            f"{len(required) - len(missing)}/{len(required)} present"
            + (f"; missing={missing}" if missing else ""),
        )
    )

    declared = set(contract.source_columns)
    extra = sorted(present - declared)
    allowed = contract.validation.allow_extra_columns
    checks.append(
        Check(
            "extra_columns_allowed",
            allowed or not extra,
            f"{len(extra)} extra: {extra}" if extra else "none",
            "error" if not allowed else "info",
        )
    )

    pk_missing = [column for column in contract.primary_key if column not in present]
    checks.append(
        Check(
            "primary_key_columns_present",
            not pk_missing,
            f"missing={pk_missing}" if pk_missing else ",".join(contract.primary_key),
        )
    )
    return checks


def _unparseable_numeric(frame: pl.DataFrame, column: str) -> int:
    """Count string values that cannot be cast to the declared numeric type."""
    series = frame[column]
    numeric = series.cast(pl.Float64, strict=False)
    return numeric.null_count() - series.null_count()


def validate_types(frame: pl.DataFrame, contract: DatasourceContract) -> list[Check]:
    """Check observed dtypes against contract dtypes.

    Family mismatches that are safely castable are reported as warnings;
    values that cannot be cast to the declared type are reported as errors,
    which is what catches malformed numeric strings in the source.
    """
    warnings: list[str] = []
    errors: list[str] = []
    for attribute in contract.source_attributes:
        if attribute.name not in frame.columns:
            continue
        observed_dtype = frame.schema[attribute.name]
        observed = dtype_family(observed_dtype)
        expected = contract_family(attribute.dtype)

        if observed == expected:
            continue

        if observed == "string" and expected in {"integer", "float"}:
            bad = _unparseable_numeric(frame, attribute.name)
            if bad:
                errors.append(
                    f"{attribute.name}: {bad} value(s) not parseable as {attribute.dtype}"
                )
            else:
                warnings.append(f"{attribute.name}: read as string, castable to {attribute.dtype}")
            continue

        if observed == "integer" and expected == "boolean":
            values = set(frame[attribute.name].unique().to_list())
            if values - {0, 1, None}:
                errors.append(f"{attribute.name}: non 0/1 values for boolean column")
            else:
                warnings.append(f"{attribute.name}: read as integer, castable to boolean")
            continue

        if observed == "string" and expected == "boolean":
            values = set(frame[attribute.name].drop_nulls().unique().to_list())
            allowed = {"true", "false", "0", "1"}
            if {str(v).lower() for v in values} - allowed:
                errors.append(f"{attribute.name}: non boolean literals for boolean column")
            else:
                warnings.append(f"{attribute.name}: read as string, castable to boolean")
            continue

        if observed in {"integer", "float"} and expected == "string":
            warnings.append(f"{attribute.name}: read as {observed}, castable to string")
            continue

        errors.append(f"{attribute.name}: expected {attribute.dtype}, read as {observed}")

    checks = [
        Check(
            "dtypes_match_contract",
            not errors,
            "; ".join(errors) if errors else f"{len(frame.columns)} columns checked",
        )
    ]
    if warnings:
        checks.append(
            Check("dtype_casts_required", True, "; ".join(warnings[:10]), "warning")
        )
    return checks


def validate_row_counts(frame: pl.DataFrame, contract: DatasourceContract) -> list[Check]:
    """Compare observed shape with the contract's declared expectations."""
    checks: list[Check] = []
    if contract.column_count_expected is not None:
        checks.append(
            Check(
                "column_count_expected",
                frame.width == contract.column_count_expected,
                f"expected {contract.column_count_expected}, observed {frame.width}",
                "warning",
            )
        )
    if contract.row_count_expected is not None:
        checks.append(
            Check(
                "row_count_expected",
                frame.height == contract.row_count_expected,
                f"expected {contract.row_count_expected}, observed {frame.height}",
                "warning",
            )
        )
    return checks


def validate_schema(frame: pl.DataFrame, contract: DatasourceContract) -> list[Check]:
    """Run every schema-level check for a source dataframe."""
    return (
        validate_columns(frame, contract)
        + validate_types(frame, contract)
        + validate_row_counts(frame, contract)
    )


def failing_checks(checks: list[Check]) -> list[Check]:
    """Only the checks whose failure actually blocks ingestion."""
    return [check for check in checks if not check.passed and check.severity == "error"]


def null_profile(frame: pl.DataFrame, contract: DatasourceContract) -> list[dict[str, object]]:
    """Null counts per source attribute, used in the quality report."""
    profile: list[dict[str, object]] = []
    for attribute in contract.source_attributes:
        if attribute.name not in frame.columns:
            continue
        nulls = frame[attribute.name].null_count()
        profile.append(
            {
                "column": attribute.name,
                "null_count": nulls,
                "null_share": round(nulls / max(frame.height, 1), 4),
                "declared_nullable": attribute.nullable,
                "origin": attribute.origin.value,
            }
        )
    return profile


def non_nullable_violations(
    frame: pl.DataFrame, contract: DatasourceContract
) -> pl.Series | None:
    """Boolean mask of rows containing a null in a non-nullable source column."""
    mask = pl.Series("invalid", [False] * frame.height)
    for attribute in contract.source_attributes:
        if attribute.nullable or attribute.name not in frame.columns:
            continue
        mask = mask | frame[attribute.name].is_null()
    return mask


def attribute_map(contract: DatasourceContract) -> dict[str, Attribute]:
    """Name-to-attribute lookup for the source schema."""
    return {attribute.name: attribute for attribute in contract.source_attributes}
