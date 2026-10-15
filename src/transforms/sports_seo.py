"""Silver transformation for the sports SEO domain.

Normalises representation, validates rows, flags and quarantines problems,
and preserves lineage. It does *not* compute analytical features: that
belongs to the analytical layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl

from src.ingestion.contracts import DatasourceContract
from src.platform.config import PlatformConfig, load_platform_config
from src.platform.datasource_registry import DatasourceRegistry
from src.platform.lineage import LineageRecord, LineageStore
from src.platform.storage import Storage
from src.transforms.common import (
    cast_to_contract,
    dedupe_by_key,
    entity_from_domain,
    normalize_columns,
    normalize_domain,
    normalize_nulls,
    normalize_strings,
    normalize_url,
)
from src.transforms.quality import QualityResult, RowRule, evaluate_rules, pandera_schema_check

SILVER_VERSION = "1.0.0"

LINEAGE_COLUMNS = ["_run_id", "_dataset_id", "_source_checksum", "_silver_version"]


@dataclass
class SilverResult:
    """Outcome of one Silver build."""

    dataset_id: str
    run_id: str
    silver_table: str = ""
    quarantine_table: str = ""
    row_count: int = 0
    quarantine_rows: int = 0
    duplicate_rows: int = 0
    invalid_rows: int = 0
    cast_issues: list[str] = field(default_factory=list)
    quality_report: pl.DataFrame = field(default_factory=pl.DataFrame)
    transform_version: str = SILVER_VERSION

    def as_dict(self) -> dict[str, Any]:
        """Dictionary form for the CLI."""
        return {
            "dataset_id": self.dataset_id,
            "run_id": self.run_id,
            "silver_table": self.silver_table,
            "quarantine_table": self.quarantine_table,
            "row_count": self.row_count,
            "quarantine_rows": self.quarantine_rows,
            "duplicate_rows": self.duplicate_rows,
            "invalid_rows": self.invalid_rows,
            "cast_issues": self.cast_issues,
            "transform_version": self.transform_version,
        }


def silver_rules(contract: DatasourceContract) -> list[RowRule]:
    """Row-level rules for the sports SEO observation table."""
    rules = [
        RowRule(
            "primary_key_url_not_null",
            pl.col("url").is_not_null(),
            "error",
            "url is part of the primary key and must not be null",
            ["url"],
        ),
        RowRule(
            "primary_key_query_not_null",
            pl.col("query").is_not_null(),
            "error",
            "query is part of the primary key; rows without a query cannot support query-level analysis",
            ["query"],
        ),
        RowRule(
            "url_absolute",
            pl.col("url").str.starts_with("http").fill_null(False),
            "error",
            "url must be an absolute http(s) URL",
            ["url"],
        ),
        RowRule(
            "query_not_blank",
            (pl.col("query").str.len_chars() > 0).fill_null(False),
            "error",
            "query must not be blank",
            ["query"],
        ),
        RowRule(
            "serp_position_range",
            pl.col("serp_position").is_between(1, 10),
            "error",
            "serp_position must be within the observed top ten (1-10)",
            ["serp_position"],
        ),
        RowRule(
            "word_count_non_negative",
            (pl.col("word_count") >= 0).fill_null(True),
            "error",
            "word_count must be >= 0",
            ["word_count"],
        ),
        RowRule(
            "response_time_non_negative",
            (pl.col("response_time_ms") >= 0).fill_null(True),
            "warning",
            "response_time_ms must be >= 0",
            ["response_time_ms"],
        ),
        RowRule(
            "page_size_non_negative",
            (pl.col("page_size_kb") >= 0).fill_null(True),
            "warning",
            "page_size_kb must be >= 0",
            ["page_size_kb"],
        ),
    ]
    declared = set(contract.source_columns)
    return [rule for rule in rules if set(rule.columns).issubset(declared)]


def normalize_silver(frame: pl.DataFrame, contract: DatasourceContract) -> tuple[pl.DataFrame, list[str]]:
    """Apply representation-only normalisation and contract type casting."""
    issues: list[str] = []
    frame = normalize_columns(frame)
    frame = normalize_nulls(frame)
    frame = normalize_strings(frame)

    if "url" in frame.columns:
        frame = frame.with_columns(
            pl.col("url").map_elements(normalize_url, return_dtype=pl.String).alias("url")
        )
    if "domain" in frame.columns:
        frame = frame.with_columns(
            pl.col("domain").map_elements(normalize_domain, return_dtype=pl.String).alias("domain")
        )
    if "url" in frame.columns and "domain" not in frame.columns:
        frame = frame.with_columns(
            pl.col("url").map_elements(normalize_domain, return_dtype=pl.String).alias("domain")
        )

    schema = {
        attribute.name: attribute.dtype
        for attribute in contract.source_attributes
        if attribute.name in frame.columns
    }
    frame, cast_issues = cast_to_contract(frame, schema)
    issues.extend(cast_issues)

    # Entity derivation: domain-derived label (not necessarily a brand).
    if "domain" in frame.columns:
        frame = frame.with_columns(
            pl.col("domain")
            .map_elements(entity_from_domain, return_dtype=pl.String)
            .alias("entity")
        )
        if "brands" in frame.columns:
            frame = frame.with_columns(
                pl.when(pl.col("brands").is_not_null() & (pl.col("brands").str.len_chars() > 0))
                .then(pl.lit("page_brand"))
                .otherwise(pl.lit("domain_only"))
                .alias("brand_extraction_source")
            )
        else:
            frame = frame.with_columns(pl.lit("domain_only").alias("brand_extraction_source"))
    return frame, issues


def build_silver(
    dataset_id: str,
    config: PlatformConfig | None = None,
    registry: DatasourceRegistry | None = None,
) -> SilverResult:
    """Build the Silver table (and quarantine) for one dataset."""
    cfg = config or load_platform_config()
    store = Storage(cfg)
    registry = registry or DatasourceRegistry(cfg, store)
    contract = registry.get(dataset_id)

    bronze = store.bronze_table(contract.domain)
    raw = store.read(bronze)
    if raw is None or not raw.height:
        raise RuntimeError(f"no Bronze table for {dataset_id}; run ingestion first")

    last_run = registry.last_successful_run(dataset_id)
    run_id = str(last_run["run_id"]) if last_run else str(raw["_run_id"].unique()[-1])
    frame = raw.filter(pl.col("_run_id") == run_id)

    lineage = {
        "source_path": str(last_run.get("source_path", "")) if last_run else "",
        "source_checksum": str(last_run.get("source_checksum", "")) if last_run else "",
    }

    data_columns = [column for column in frame.columns if not column.startswith("_")]
    frame = frame.select(data_columns)

    # 1. normalisation ----------------------------------------------------
    frame, cast_issues = normalize_silver(frame, contract)

    # 2. duplicate detection ---------------------------------------------
    duplicates = frame.head(0)
    duplicate_count = 0
    if cfg.silver.drop_duplicates:
        frame, duplicates, duplicate_count = dedupe_by_key(frame, contract.primary_key)

    # 3. row-level quality ------------------------------------------------
    rules = silver_rules(contract)
    outcome = evaluate_rules(frame, rules, dataset_id=dataset_id, run_id=run_id)
    frame = outcome.frame

    results: list[QualityResult] = [
        QualityResult(
            check_name="primary_key_unique",
            passed=duplicate_count == 0,
            failed_rows=duplicate_count,
            severity="error",
            message=(
                f"{duplicate_count} duplicate primary key row(s) on "
                f"({', '.join(contract.primary_key)})"
            ),
            dataset_id=dataset_id,
            run_id=run_id,
        )
    ]
    results.extend(outcome.results)
    structural = pandera_schema_check(
        frame.select(
            [c for c in ("url", "domain", "serp_position", "word_count") if c in frame.columns]
        )
    )
    structural.dataset_id = dataset_id
    structural.run_id = run_id
    results.append(structural)

    # 4. quarantine -------------------------------------------------------
    invalid = frame.filter(pl.col("is_valid").not_())
    quarantine_parts: list[pl.DataFrame] = []
    if duplicate_count:
        dup_marked = duplicates.with_columns(
            pl.lit(False).alias("is_valid"),
            pl.lit(1).alias("validation_error_count"),
            pl.lit("duplicate_primary_key").alias("validation_errors"),
        )
        quarantine_parts.append(dup_marked)
    if invalid.height:
        quarantine_parts.append(invalid)
    quarantine = (
        pl.concat(quarantine_parts, how="diagonal_relaxed")
        if quarantine_parts
        else frame.head(0)
    )

    # 5. lineage columns ---------------------------------------------------
    frame = frame.with_columns(
        [
            pl.lit(run_id).alias("_run_id"),
            pl.lit(dataset_id).alias("_dataset_id"),
            pl.lit(lineage["source_checksum"]).alias("_source_checksum"),
            pl.lit(SILVER_VERSION).alias("_silver_version"),
        ]
    )
    if quarantine.height:
        quarantine = quarantine.with_columns(
            [
                pl.lit(run_id).alias("_run_id"),
                pl.lit(dataset_id).alias("_dataset_id"),
                pl.lit(lineage["source_checksum"]).alias("_source_checksum"),
                pl.lit(SILVER_VERSION).alias("_silver_version"),
                pl.lit("sports_seo").alias("_domain"),
            ]
        )

    # 6. publish -----------------------------------------------------------
    silver_path = store.silver_table(contract.domain)
    quarantine_path = store.quarantine_table(contract.domain)
    store.write(silver_path, frame, mode="overwrite")
    if quarantine.height:
        store.write(quarantine_path, quarantine, mode="overwrite")
    elif store.exists(quarantine_path):
        store.write(quarantine_path, quarantine.head(0), mode="overwrite")

    report = (
        pl.DataFrame([result.as_row() for result in results])
        if results
        else pl.DataFrame()
    )
    if report.height:
        report = report.with_columns(
            pl.lit(SILVER_VERSION).alias("transform_version")
        )

    result = SilverResult(
        dataset_id=dataset_id,
        run_id=run_id,
        silver_table=str(silver_path.relative_to(cfg.repo_root)),
        quarantine_table=str(quarantine_path.relative_to(cfg.repo_root)),
        row_count=frame.height,
        quarantine_rows=quarantine.height,
        duplicate_rows=duplicate_count,
        invalid_rows=outcome.invalid_rows,
        cast_issues=cast_issues,
        quality_report=report,
    )

    LineageStore(cfg, store).record(
        LineageRecord(
            table=f"silver/{contract.domain}",
            layer="silver",
            run_id=run_id,
            dataset_id=dataset_id,
            dataset_version=contract.version,
            source_path=lineage["source_path"],
            source_checksum=lineage["source_checksum"],
            parent_tables=[f"bronze/{contract.domain}"],
            transformation_version=SILVER_VERSION,
            row_count=frame.height,
        )
    )
    return result


def load_silver(
    dataset_id: str,
    config: PlatformConfig | None = None,
    registry: DatasourceRegistry | None = None,
    valid_only: bool = False,
) -> pl.DataFrame:
    """Read the Silver table for a dataset, optionally keeping valid rows only."""
    cfg = config or load_platform_config()
    registry = registry or DatasourceRegistry(cfg, Storage(cfg))
    contract = registry.get(dataset_id)
    frame = Storage(cfg).read(cfg.resolve(f"{cfg.paths.silver_dir}/{contract.domain}"))
    if frame is None:
        raise RuntimeError(f"no Silver table for {dataset_id}; run `silver build` first")
    if valid_only and "is_valid" in frame.columns:
        frame = frame.filter(pl.col("is_valid"))
    return frame
