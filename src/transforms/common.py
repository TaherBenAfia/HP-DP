"""Domain-independent Silver normalisation helpers.

These functions only change representation, never meaning: they do not drop
rows, impute values or invent data.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import polars as pl

TRUE_LITERALS = {"true", "t", "yes", "y", "1"}
FALSE_LITERALS = {"false", "f", "no", "n", "0"}

_WHITESPACE = re.compile(r"\s+")
_SNAKE = re.compile(r"[^0-9a-zA-Z]+")

_URL_LIKE = re.compile(r"^https?://", re.IGNORECASE)


def normalize_column_name(name: str) -> str:
    """Lowercase, strip and snake_case a column name."""
    cleaned = _SNAKE.sub("_", name.strip().lower()).strip("_")
    return re.sub(r"_+", "_", cleaned)


def normalize_columns(frame: pl.DataFrame) -> pl.DataFrame:
    """Rename every column to the platform naming convention."""
    return frame.rename({column: normalize_column_name(column) for column in frame.columns})


def normalize_text(series: pl.Series) -> pl.Series:
    """Trim and collapse internal whitespace in a string column."""
    if series.dtype not in (pl.String, pl.Categorical):
        return series
    return series.str.strip_chars().str.replace_all(_WHITESPACE, " ")


def normalize_nulls(frame: pl.DataFrame, null_values: list[str] | None = None) -> pl.DataFrame:
    """Map common null literals to real nulls in string columns."""
    literals = [value for value in (null_values or []) if value != ""]
    if not literals:
        return frame
    updates: list[pl.Expr] = []
    for column, dtype in frame.schema.items():
        if dtype == pl.String:
            updates.append(
                pl.when(pl.col(column).str.strip_chars().is_in(literals))
                .then(None)
                .otherwise(pl.col(column))
                .alias(column)
            )
    return frame.with_columns(updates) if updates else frame


def normalize_strings(frame: pl.DataFrame) -> pl.DataFrame:
    """Trim whitespace in every string column."""
    updates = [
        pl.col(column).str.strip_chars().alias(column)
        for column, dtype in frame.schema.items()
        if dtype == pl.String
    ]
    return frame.with_columns(updates) if updates else frame


def normalize_boolean(series: pl.Series) -> pl.Series:
    """Coerce boolean-ish values (0/1, true/false, yes/no) to Boolean."""
    if series.dtype == pl.Boolean:
        return series
    if series.dtype in (pl.Int64, pl.Int32, pl.UInt8, pl.UInt64, pl.Float64, pl.Int8, pl.Int16):
        return series.cast(pl.Boolean, strict=False)
    if series.dtype == pl.String:
        lowered = series.str.to_lowercase().str.strip_chars()
        mapped = (
            pl.when(lowered.is_in(list(TRUE_LITERALS)))
            .then(True)
            .when(lowered.is_in(list(FALSE_LITERALS)))
            .then(False)
            .otherwise(None)
            .alias(series.name)
        )
        return series.to_frame().select(mapped).to_series()
    return series.cast(pl.Boolean, strict=False)


def normalize_numeric(series: pl.Series) -> tuple[pl.Series, pl.Series]:
    """Cast to Float64 without raising; returns (values, unparseable_mask)."""
    if series.dtype.is_integer() or series.dtype.is_float():
        return series.cast(pl.Float64), pl.Series(series.name, [False] * series.len())
    if series.dtype == pl.Boolean:
        return series.cast(pl.Float64), pl.Series(series.name, [False] * series.len())
    if series.dtype == pl.String:
        stripped = series.str.strip_chars()
        numeric = stripped.cast(pl.Float64, strict=False)
        malformed = numeric.is_null() & stripped.is_not_null() & stripped.ne("")
        return numeric, malformed
    return series.cast(pl.Float64, strict=False), pl.Series(
        series.name, [False] * series.len()
    )


def normalize_url(value: str | None) -> str | None:
    """Trim, drop the fragment and normalise scheme/host casing."""
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if not _URL_LIKE.match(candidate):
        candidate = f"https://{candidate}"
    parts = urlsplit(candidate)
    host = parts.netloc.lower()
    if host.endswith(":80") and parts.scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and parts.scheme == "https":
        host = host[:-4]
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def normalize_domain(value: str | None) -> str | None:
    """Extract the lowercase host from a URL or host string."""
    if value is None:
        return None
    candidate = value.strip().lower()
    if not candidate:
        return None
    if "://" in candidate:
        candidate = urlsplit(candidate).netloc
    candidate = candidate.split("/")[0].split(":")[0]
    return candidate.removeprefix("www.") or None


def entity_from_domain(domain: str | None) -> str | None:
    """Derive a stable entity label from a registrable domain.

    This is a *domain-derived* label: it is not necessarily a manufacturer
    brand, and the platform documents that distinction everywhere.
    """
    if not domain:
        return None
    labels = [label for label in domain.split(".") if label]
    if not labels:
        return None
    if len(labels) >= 3 and labels[-2] in {"com", "co", "org", "net", "gov"}:
        core = labels[-3]
    elif len(labels) >= 2:
        core = labels[-2]
    else:
        core = labels[0]
    return core.capitalize() or None


def dedupe_by_key(
    frame: pl.DataFrame, primary_key: list[str]
) -> tuple[pl.DataFrame, pl.DataFrame, int]:
    """Split a frame into (kept, duplicates, duplicate_count) on the primary key.

    The first occurrence of each key is kept; later occurrences are returned
    as duplicates so the caller can quarantine them explicitly.
    """
    if not primary_key or any(column not in frame.columns for column in primary_key):
        return frame, frame.head(0), 0
    key_expr = pl.struct(
        [
            pl.col(column).fill_null(f"__NULL__{index}").cast(pl.String)
            for index, column in enumerate(primary_key)
        ]
    ).alias("__key__")
    keyed = frame.with_columns(key_expr)
    keep_mask = keyed.select(pl.col("__key__").is_first_distinct()).to_series()
    kept = frame.filter(keep_mask)
    duplicate_rows = frame.filter(~keep_mask)
    return kept, duplicate_rows, duplicate_rows.height


def row_flag(mask: pl.Series, name: str) -> pl.Series:
    """Utility used by quality checks to name a boolean flag."""
    return mask.cast(pl.Boolean).fill_null(False).alias(name)


def cast_to_contract(
    frame: pl.DataFrame, schema: dict[str, str]
) -> tuple[pl.DataFrame, list[str]]:
    """Cast columns towards the contract dtypes.

    Returns the frame plus a list of human-readable cast issues; a cast that
    cannot be done safely leaves the column untouched and reports the issue
    instead of corrupting data.
    """
    issues: list[str] = []
    updates: list[pl.Expr] = []
    for column, contract_dtype in schema.items():
        if column not in frame.columns:
            continue
        current = frame.schema[column]
        lowered = contract_dtype.lower()

        if lowered in {"bool", "boolean"}:
            updates.append(normalize_boolean(frame[column]).alias(column))
            continue
        if lowered in {"object", "string", "str"}:
            if current != pl.String:
                updates.append(pl.col(column).cast(pl.String, strict=False).alias(column))
            continue
        if lowered.startswith("float"):
            if current.is_integer() or current.is_float():
                continue
            if current == pl.String:
                values, malformed = normalize_numeric(frame[column])
                if int(malformed.sum()) == 0:
                    updates.append(values.alias(column))
                else:
                    issues.append(
                        f"{column}: {int(malformed.sum())} unparseable value(s) kept as text"
                    )
                continue
            issues.append(f"{column}: cannot cast {current} to {contract_dtype}")
            continue
        if lowered.startswith("int"):
            if current.is_integer():
                continue
            if current.is_float():
                values = frame[column]
                integral = bool(((values % 1) == 0).fill_null(True).all())
                if integral:
                    updates.append(values.cast(pl.Int64, strict=False).alias(column))
                else:
                    issues.append(f"{column}: fractional values kept as float")
                continue
            if current == pl.String:
                values, malformed = normalize_numeric(frame[column])
                if int(malformed.sum()) == 0:
                    updates.append(values.cast(pl.Int64, strict=False).alias(column))
                else:
                    issues.append(
                        f"{column}: {int(malformed.sum())} unparseable value(s) kept as text"
                    )
                continue
            issues.append(f"{column}: cannot cast {current} to {contract_dtype}")
    if updates:
        frame = frame.with_columns(updates)
    return frame, issues


def value_counts(frame: pl.DataFrame, column: str) -> dict[Any, int]:
    """Small helper for reporting categorical distributions."""
    if column not in frame.columns:
        return {}
    grouped = frame.group_by(column).len().sort("len", descending=True)
    return {row[column]: int(row["len"]) for row in grouped.to_dicts()}
