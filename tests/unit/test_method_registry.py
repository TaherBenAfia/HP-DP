"""Unit tests for the analytical and statistical method registry."""

import polars as pl

from src.analytics.method_registry import (
    create_default_method_registry,
    run_descriptive_summary,
    run_statistical_evidence,
)


def test_method_registry_capability_matching() -> None:
    """Methods match only when required columns and minimum sample sizes are met."""
    registry = create_default_method_registry()

    # Case 1: Adequate sample size and required columns present
    cols_full = {"rank", "page_speed_score", "word_count"}
    matches = registry.match(available_columns=cols_full, row_count=50)
    match_ids = [m.method_id for m in matches]
    assert "descriptive_statistics_v1" in match_ids
    assert "associational_evidence_fdr_v1" in match_ids

    # Case 2: Missing required column for Method B ('page_speed_score')
    cols_partial = {"rank", "word_count"}
    matches_partial = registry.match(available_columns=cols_partial, row_count=50)
    match_partial_ids = [m.method_id for m in matches_partial]
    assert "descriptive_statistics_v1" in match_partial_ids
    assert "associational_evidence_fdr_v1" not in match_partial_ids

    # Case 3: Insufficient sample size (row_count = 3 < min_observations)
    matches_small = registry.match(available_columns=cols_full, row_count=3)
    assert len(matches_small) == 0

    # Case 4: Objective filter
    matches_obj = registry.match(
        available_columns=cols_full,
        row_count=50,
        objective="baseline_summary",
    )
    assert len(matches_obj) == 1
    assert matches_obj[0].method_id == "descriptive_statistics_v1"


def test_descriptive_summary_execution() -> None:
    """Method A executes and produces correct descriptive statistical aggregates."""
    df = pl.DataFrame(
        {
            "rank": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "page_speed_score": [80.0, 85.0, 90.0, 75.0, 70.0, 65.0, 95.0, 60.0, 88.0, 92.0],
            "domain": ["a.com"] * 10,
        }
    )
    summary_df = run_descriptive_summary(df)
    assert summary_df.height == 2  # rank and page_speed_score
    features = summary_df["feature"].to_list()
    assert "rank" in features
    assert "page_speed_score" in features
    assert "mean" in summary_df.columns
    assert "std" in summary_df.columns


def test_statistical_evidence_execution() -> None:
    """Method B executes real Spearman correlation with FDR correction and associational limits."""
    # Synthesize data where page_speed has strong monotonic rank association (n >= min_observations=30)
    ranks = list(range(1, 36))
    speeds = [100.0 - (r * 2.0) for r in ranks]  # Negative correlation with rank
    df = pl.DataFrame({"rank": ranks, "page_speed_score": speeds})

    evidence_df = run_statistical_evidence(df)
    assert evidence_df.height >= 1
    assert "p_value" in evidence_df.columns
    assert "q_value" in evidence_df.columns
    assert "significant_fdr" in evidence_df.columns
    assert "test" in evidence_df.columns
    assert "spearman" in evidence_df["test"].to_list()
