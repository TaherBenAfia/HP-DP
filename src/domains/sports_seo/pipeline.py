"""End-to-end Gold publishing pipeline for the sports SEO domain.

Stages: Silver -> features (visibility) -> statistics -> actions -> metrics ->
publish (Delta + manifests + lineage). Every published table is stamped with
its data-product version so a consumer can always tell which pipeline run

# type: ignore
produced the rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from src.analytics.feature_registry import FeatureRole
from src.domains.base import DomainContext
from src.domains.sports_seo.domain import SportsSEODomain
from src.domains.sports_seo.features import FEATURE_REGISTRY
from src.domains.sports_seo.schema import valid_observations
from src.ingestion.fingerprint import new_run_id
from src.platform.config import PlatformConfig, load_platform_config
from src.platform.datasource_registry import DatasourceRegistry
from src.platform.lineage import LineageRecord, LineageStore
from src.platform.storage import Storage
from src.platform.versioning import build_version, product_manifest, stamp
from src.transforms.sports_seo import SILVER_VERSION, load_silver


@dataclass
class PipelineResult:
    """Everything a caller (CLI, flow, dashboard) needs to report on."""

    dataset_id: str
    domain: str
    run_id: str
    rows: int
    tables: dict[str, str] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)


def _quality_tables(
    silver: pl.DataFrame, analysis: pl.DataFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Per-column profile plus scalar run summary for the Gold layer."""
    rows: list[dict[str, Any]] = []
    total = silver.height
    for column in silver.columns:
        series = silver[column]
        nulls = series.null_count()
        unique = series.n_unique()
        spec = FEATURE_REGISTRY.get(column)
        rows.append(
            {
                "column": column,
                "dtype": str(series.dtype),
                "null_count": nulls,
                "null_share": (nulls / total) if total else 0.0,
                "n_unique": unique,
                "constant": unique < 2,
                "family": spec.family if spec else "",
                "role": spec.role.value if spec else "",
                "in_action_families": bool(
                    spec and spec.role in {FeatureRole.FEATURE, FeatureRole.COMPETITOR}
                ),
            }
        )
    profile = pl.DataFrame(rows)

    constants = [row["column"] for row in rows if row["constant"]]
    summary = pl.DataFrame(
        {
            "metric": [
                "silver_rows",
                "valid_rows",
                "invalid_rows",
                "columns",
                "constant_columns",
                "analysed_features",
                "null_cells",
                "duplicate_primary_keys",
            ],
            "value": [
                float(total),
                float(analysis.height),
                float(total - analysis.height),
                float(len(rows)),
                float(len(constants)),
                float(len(FEATURE_REGISTRY)),
                float(sum(row["null_count"] for row in rows)),
                0.0,
            ],
            "detail": [""] * 7 + [json.dumps({"constant_columns": constants})],
        }
    )
    return profile, summary


def run(
    dataset_id: str = "sports_seo_tunisia_v1",
    config: PlatformConfig | None = None,
    registry: DatasourceRegistry | None = None,
) -> PipelineResult:
    """Build and publish every Gold table for one dataset."""
    cfg = config or load_platform_config()
    store = Storage(cfg)
    reg = registry or DatasourceRegistry(cfg, store)
    domain = SportsSEODomain(DomainContext(config=cfg, registry=reg, dataset_id=dataset_id))

    # 1. Silver ------------------------------------------------------------
    silver = load_silver(dataset_id, cfg, reg)
    valid = valid_observations(silver)
    # Gold runs reuse the Silver run id so lineage edges stay joinable.
    run_id = (
        str(silver["_run_id"][0])
        if "_run_id" in silver.columns and silver.height
        else new_run_id(dataset_id)
    )

    # 2. Features ----------------------------------------------------------
    analysis = domain.build_features(valid)

    # 3. Statistics --------------------------------------------------------
    evidence = domain.run_statistics(analysis)

    # 4. Actions -----------------------------------------------------------
    actions = domain.build_actions(analysis, evidence)
    key_columns = [column for column in ("url", "query") if column in actions.columns]
    new_action_columns = [
        column
        for column in actions.columns
        if column not in key_columns and column not in analysis.columns
    ]
    analysis = analysis.join(
        actions.select(key_columns + new_action_columns), on=key_columns, how="left"
    )

    # 5. Metrics -----------------------------------------------------------
    metrics = domain.build_metrics(analysis)

    # 6. Quality -----------------------------------------------------------
    profile, quality_summary = _quality_tables(silver, analysis)

    # 7. Publish -----------------------------------------------------------
    last_run = reg.last_successful_run(dataset_id) or {}
    source_checksum = str(last_run.get("source_checksum", ""))
    dataset_version = str(last_run.get("dataset_version", "0.0.0")) or "0.0.0"

    tables: dict[str, pl.DataFrame] = {
        **metrics,
        "statistical_evidence": evidence,
        "action_recommendations": actions,
        "data_quality": profile,
        "quality_summary": quality_summary,
    }

    identity_columns = [
        column
        for column in (
            "url",
            "domain",
            "entity",
            "query",
            "serp_position",
            "svtp_score",
            "page_trend",
            "rank_group",
            "primary_action",
            "secondary_action",
            "primary_action_score",
            "action_priority",
        )
        if column in analysis.columns
    ]
    feature_columns = [
        name
        for name in FEATURE_REGISTRY.names()
        if name in analysis.columns and name not in identity_columns
    ]
    observation_columns = identity_columns + feature_columns + [
        column
        for column in analysis.columns
        if column.endswith("_action_score") and column not in identity_columns
    ]
    tables["observations"] = analysis.select(observation_columns)

    published: dict[str, str] = {}
    manifests_dir = cfg.resolve(cfg.paths.sharing_manifest_dir) / dataset_id
    manifests_dir.mkdir(parents=True, exist_ok=True)
    version = build_version(
        product_id=f"{domain.domain}.gold",
        source_dataset_version=dataset_version,
        config=cfg,
    )

    lineage = LineageStore(cfg, store)
    silver_table = f"silver/{domain.domain}"
    parents_by_table: dict[str, list[str]] = {
        "entity_metrics": [silver_table, "gold/action_recommendations"],
        "query_metrics": [silver_table],
        "page_metrics": [silver_table],
        "action_distribution": [silver_table, "gold/action_recommendations"],
        "action_summary": [silver_table, "gold/action_recommendations"],
        "visibility_summary": [silver_table],
        "rank_group_summary": [silver_table],
        "statistical_evidence": [silver_table],
        "action_recommendations": [silver_table, "gold/statistical_evidence"],
        "data_quality": [silver_table],
        "quality_summary": [silver_table],
        "observations": [silver_table],
    }

    for name, frame in tables.items():
        stamped = stamp(frame, version)
        if name == "observations":
            path = store.write(store.gold_domain_table(domain.domain, name), stamped, mode="overwrite")
        else:
            path = store.write(store.gold_table(name), stamped, mode="overwrite")
        relative = str(path.relative_to(cfg.repo_root))
        published[name] = relative

        manifest = product_manifest(version, stamped.height, relative)
        manifest["dataset_id"] = dataset_id
        manifest["source_checksum"] = source_checksum
        (manifests_dir / f"{name}.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        lineage.record(
            LineageRecord(
                table=(
                    f"gold/{domain.domain}/{name}" if name == "observations" else f"gold/{name}"
                ),
                layer="gold",
                run_id=run_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                source_path=str(last_run.get("source_path", "")),
                source_checksum=source_checksum,
                parent_tables=parents_by_table.get(name, [silver_table]),
                transformation_version=SILVER_VERSION,
                analytical_model_version=domain.domain_version,
                pipeline_version=cfg.platform.pipeline_version,
                row_count=stamped.height,
            )
        )

    significant_rows = 0
    if evidence.height:
        significant_rows = int(
            evidence.filter(
                pl.col("status").eq("ok") & pl.col("significant_fdr")
            ).height
        )

    report = {
        "dataset_id": dataset_id,
        "domain": domain.domain,
        "run_id": run_id,
        "silver_rows": silver.height,
        "valid_rows": analysis.height,
        "invalid_rows": silver.height - analysis.height,
        "entities": analysis["entity"].n_unique() if "entity" in analysis.columns else 0,
        "queries": analysis["query"].n_unique() if "query" in analysis.columns else 0,
        "documents": analysis["url"].n_unique() if "url" in analysis.columns else 0,
        "statistical_tests": evidence.height,
        "significant_tests": significant_rows,
        "demand_component_available": False,
        "published_tables": published,
        "source_checksum": source_checksum,
        "product_version": version.product_version,
        "manifests_dir": str(manifests_dir.relative_to(cfg.repo_root)),
    }
    if "primary_action" in analysis.columns:
        report["primary_action_distribution"] = {
            str(action): int(count)
            for action, count in analysis.group_by("primary_action").len().iter_rows()
        }

    return PipelineResult(
        dataset_id=dataset_id,
        domain=domain.domain,
        run_id=run_id,
        rows=analysis.height,
        tables=published,
        report=report,
    )
