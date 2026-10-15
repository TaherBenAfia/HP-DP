"""Gold metric tables for the sports SEO domain."""

from __future__ import annotations

import polars as pl

# type: ignore
from src.analytics.metrics import action_distribution, entity_metrics, page_metrics, query_metrics
from src.domains.sports_seo.actions import FAMILIES
from src.domains.sports_seo.schema import ANALYSIS_KEY

DEFAULT_METRICS = ["svtp_score", "serp_position", "primary_action_score"]
FAMILY_SCORE_COLUMNS = [f"{family.name}_action_score" for family in FAMILIES]


def _with_family_means(frame: pl.DataFrame, grouped: pl.DataFrame) -> pl.DataFrame:
    """Attach mean per-family action scores to a grouped metric table."""
    columns = [column for column in FAMILY_SCORE_COLUMNS if column in frame.columns]
    if not columns:
        return grouped
    entity_column = grouped.columns[0]
    means = frame.group_by(entity_column).agg(
        [pl.col(column).mean().alias(f"{column}_mean") for column in columns]
    )
    return grouped.join(means, on=entity_column, how="left")


def build_metrics(frame: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Produce every Gold metric table from an analysis frame."""
    metrics = [column for column in DEFAULT_METRICS if column in frame.columns]

    entities = entity_metrics(frame, ANALYSIS_KEY, metrics)
    entities = entities.join(
        frame.group_by(ANALYSIS_KEY.entity)
        .agg(
            pl.col(ANALYSIS_KEY.document).n_unique().alias("documents"),
            pl.col(ANALYSIS_KEY.query).n_unique().alias("queries"),
        ),
        on=ANALYSIS_KEY.entity,
        how="left",
    )
    entities = _with_family_means(frame, entities)

    queries = query_metrics(frame, ANALYSIS_KEY, metrics)
    queries = queries.join(
        frame.group_by(ANALYSIS_KEY.query)
        .agg(
            pl.col("domain").n_unique().alias("domains"),
            pl.col(ANALYSIS_KEY.entity).n_unique().alias("entities"),
        ),
        on=ANALYSIS_KEY.query,
        how="left",
    )

    pages = page_metrics(frame, ANALYSIS_KEY, metrics)
    pages = pages.join(
        frame.group_by(ANALYSIS_KEY.document)
        .agg(
            pl.col(ANALYSIS_KEY.entity).first().alias(ANALYSIS_KEY.entity),
            pl.col(ANALYSIS_KEY.query).n_unique().alias("queries"),
        ),
        on=ANALYSIS_KEY.document,
        how="left",
    )

    tables: dict[str, pl.DataFrame] = {
        "entity_metrics": entities,
        "query_metrics": queries,
        "page_metrics": pages,
    }

    distribution = action_distribution(frame, ANALYSIS_KEY)
    if distribution is not None:
        tables["action_distribution"] = distribution

    if "primary_action" in frame.columns:
        tables["action_summary"] = (
            frame.group_by("primary_action")
            .agg(
                pl.len().alias("observations"),
                pl.col("primary_action_score").mean().alias("primary_action_score_mean"),
                pl.col("action_priority").eq("high").sum().alias("priority_high"),
                pl.col("action_priority").eq("medium").sum().alias("priority_medium"),
                pl.col("action_priority").eq("low").sum().alias("priority_low"),
            )
            .sort("observations", descending=True)
        )

    if "svtp_score" in frame.columns:
        tables["visibility_summary"] = pl.DataFrame(
            {
                "metric": [
                    "observations",
                    "svtp_mean",
                    "svtp_median",
                    "svtp_min",
                    "svtp_max",
                    "svtp_p25",
                    "svtp_p75",
                    "mean_serp_position",
                    "top3_share",
                ],
                "value": [
                    float(frame.height),
                    float(frame["svtp_score"].mean() or 0.0),
                    float(frame["svtp_score"].median() or 0.0),
                    float(frame["svtp_score"].min() or 0.0),
                    float(frame["svtp_score"].max() or 0.0),
                    float(frame["svtp_score"].quantile(0.25) or 0.0),
                    float(frame["svtp_score"].quantile(0.75) or 0.0),
                    float(frame["serp_position"].mean() or 0.0)
                    if "serp_position" in frame.columns
                    else 0.0,
                    float(
                        (
                            frame.filter(pl.col("serp_position") <= 3).height / frame.height
                        )
                        if frame.height and "serp_position" in frame.columns
                        else 0.0
                    ),
                ],
            }
        )

    if "rank_group" in frame.columns:
        tables["rank_group_summary"] = (
            frame.group_by("rank_group")
            .agg(pl.len().alias("observations"))
            .sort("rank_group")
        )

    return tables
