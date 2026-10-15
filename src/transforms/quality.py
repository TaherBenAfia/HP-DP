"""Row-level data-quality engine.

Design rules:

* invalid rows are *flagged and quarantined*, never silently dropped;
* every check produces a row in the Gold ``data_quality`` report with
  ``dataset_id``, ``run_id``, ``check_name``, ``passed``, ``failed_rows``,
  ``severity`` and ``message``;
* structural checks use Pandera, identity/range records use Pydantic, and
  everything row-specific is expressed as Polars expressions so the engine
  stays dataframe-native.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, Field, field_validator

Severity = Literal["error", "warning", "info"]


# ---------------------------------------------------------------------------
# Pydantic: record-level identity validation
# ---------------------------------------------------------------------------
class ObservationIdentity(BaseModel):
    """Identity of a single observation record."""

    url: str = Field(min_length=8, description="Absolute page URL")
    query: str = Field(min_length=1, description="Search query")

    @field_validator("url")
    @classmethod
    def _url_looks_absolute(cls, value: str) -> str:
        """Require a URL scheme so downstream parsing cannot fail."""
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must be absolute (http/https)")
        return value

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        """Reject blank queries after whitespace normalisation."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned


def validate_identity(url: Any, query: Any) -> str | None:
    """Return an error message when the identity record is invalid."""
    try:
        ObservationIdentity(url=str(url), query=str(query))
    except Exception as exc:  # noqa: BLE001 - message is the output
        return str(exc).replace("\n", " ")[:200]
    return None


# ---------------------------------------------------------------------------
# Row rules
# ---------------------------------------------------------------------------
@dataclass
class RowRule:
    """A rule evaluated per row. ``expr`` must evaluate to true when valid."""

    name: str
    expr: pl.Expr
    severity: Severity = "error"
    message: str = ""
    columns: list[str] = field(default_factory=list)


@dataclass
class QualityResult:
    """Aggregated outcome of one check."""

    check_name: str
    passed: bool
    failed_rows: int
    severity: Severity
    message: str
    dataset_id: str = ""
    run_id: str = ""

    def as_row(self) -> dict[str, Any]:
        """Row for the Gold data-quality report."""
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "check_name": self.check_name,
            "passed": self.passed,
            "failed_rows": self.failed_rows,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass
class QualityOutcome:
    """Everything the Silver layer needs from a quality pass."""

    frame: pl.DataFrame
    results: list[QualityResult] = field(default_factory=list)
    quarantine_mask: pl.Series | None = None

    @property
    def invalid_rows(self) -> int:
        """Rows failing at least one error-severity rule."""
        if self.quarantine_mask is None:
            return 0
        return int(self.quarantine_mask.sum())

    @property
    def report(self) -> pl.DataFrame:
        """Gold-shaped quality report."""
        return pl.DataFrame([result.as_row() for result in self.results]) if self.results else pl.DataFrame(
            schema={
                "dataset_id": pl.String,
                "run_id": pl.String,
                "check_name": pl.String,
                "passed": pl.Boolean,
                "failed_rows": pl.Int64,
                "severity": pl.String,
                "message": pl.String,
            }
        )


def evaluate_rules(
    frame: pl.DataFrame,
    rules: list[RowRule],
    dataset_id: str = "",
    run_id: str = "",
) -> QualityOutcome:
    """Evaluate row rules and attach quality flags to the frame."""
    flag_columns: list[pl.Expr] = []
    results: list[QualityResult] = []
    valid_expr = pl.lit(True)
    error_messages: list[pl.Expr] = []

    for rule in rules:
        passed = rule.expr.fill_null(False).cast(pl.Boolean)
        flag_name = f"qf_{rule.name}"
        flag_columns.append(passed.alias(flag_name))
        failed = frame.select(passed.not_().sum()).to_series()[0]
        results.append(
            QualityResult(
                check_name=rule.name,
                passed=int(failed) == 0,
                failed_rows=int(failed),
                severity=rule.severity,
                message=rule.message or f"{int(failed)} row(s) failed {rule.name}",
                dataset_id=dataset_id,
                run_id=run_id,
            )
        )
        if rule.severity == "error":
            valid_expr = valid_expr & passed
        error_messages.append(
            pl.when(passed.fill_null(False)).then(None).otherwise(pl.lit(rule.name))
        )

    annotated = frame.with_columns(flag_columns).with_columns(
        valid_expr.fill_null(False).cast(pl.Boolean).alias("is_valid")
    )
    if error_messages:
        annotated = annotated.with_columns(
            pl.concat_list(error_messages)
            .list.drop_nulls()
            .list.unique()
            .alias("__errors__"),
        ).with_columns(
            pl.col("__errors__").list.len().alias("validation_error_count"),
            pl.col("__errors__").list.join("|").fill_null("").alias("validation_errors"),
        ).drop("__errors__")
    else:
        annotated = annotated.with_columns(
            pl.lit(0).alias("validation_error_count"),
            pl.lit("").alias("validation_errors"),
        )

    return QualityOutcome(
        frame=annotated,
        results=results,
        quarantine_mask=annotated["is_valid"].not_(),
    )


# ---------------------------------------------------------------------------
# Pandera: structural schema check on the core analytical columns
# ---------------------------------------------------------------------------
def pandera_schema_check(frame: pl.DataFrame) -> QualityResult:
    """Validate core columns with Pandera (structure, not business rules)."""
    try:
        import pandera.polars as pa
        from pandera.typing.polars import Series
    except Exception as exc:  # noqa: BLE001 - pandera optional at import time
        return QualityResult(
            check_name="pandera_structural_schema",
            passed=False,
            failed_rows=0,
            severity="warning",
            message=f"pandera unavailable: {exc}",
        )

    class CoreObservationSchema(pa.DataFrameModel):
        """Structural contract for a Silver observation table."""

        url: Series[str] = pa.Field(nullable=False, description="Absolute page URL")
        domain: Series[str] = pa.Field(nullable=False, description="Host of the page URL")
        serp_position: Series[int] = pa.Field(nullable=True, ge=1, le=10)
        word_count: Series[float] = pa.Field(nullable=True, ge=0)

        class Config:
            """Strict on the columns that exist, silent on the rest."""

            strict = False
            coerce = False

    present = [name for name in ("url", "domain", "serp_position", "word_count")
               if name in frame.columns]
    if len(present) < 2:
        return QualityResult(
            check_name="pandera_structural_schema",
            passed=True,
            failed_rows=0,
            severity="info",
            message="core columns absent; structural check skipped",
        )
    try:
        CoreObservationSchema.validate(frame, lazy=True)
    except Exception as exc:  # noqa: BLE001 - pandera raises SchemaError(s)
        failures = getattr(exc, "failure_cases", None)
        count = len(failures) if failures is not None else 1
        return QualityResult(
            check_name="pandera_structural_schema",
            passed=False,
            failed_rows=int(count),
            severity="error",
            message=str(exc).replace("\n", " ")[:300],
        )
    return QualityResult(
        check_name="pandera_structural_schema",
        passed=True,
        failed_rows=0,
        severity="info",
        message=f"core columns validated: {', '.join(present)}",
    )
