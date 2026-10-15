"""Search Visibility Trend Proxy (SVTP) model tests."""

from __future__ import annotations

import polars as pl

from src.analytics.visibility import DemandSignal, VisibilityModel


def _frame(rows: int = 10) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "url": [f"https://example.test/{i}" for i in range(rows)],
            "query": ["q"] * rows,
            "entity": [f"E{i // 2}" for i in range(rows)],
            "serp_position": list(range(1, rows + 1)),
        }
    )


def test_rank_score_is_normalised() -> None:
    result = VisibilityModel().compute(_frame())
    frame = result.frame
    assert frame["rank_score"].min() > 0
    assert frame["rank_score"].max() <= 1
    assert frame["rank_score_norm"].min() >= 0
    assert frame["rank_score_norm"].max() <= 1


def test_better_position_scores_higher() -> None:
    frame = VisibilityModel().compute(_frame()).frame.sort("serp_position")
    scores = frame["rank_score"].to_list()
    assert scores == sorted(scores, reverse=True)


def test_page_trend_is_balanced() -> None:
    frame = VisibilityModel().compute(_frame(rows=10)).frame
    counts = dict(frame["page_trend"].value_counts().iter_rows())
    assert counts[0] == counts[1] == 5


def test_demand_component_is_flagged_unavailable() -> None:
    result = VisibilityModel().compute(_frame())
    frame = result.frame
    assert result.metadata["demand_component_available"] is False
    assert frame["demand_component_available"].unique().to_list() == [False]
    assert frame["demand_score_norm"].unique().to_list() == [0.5]


def test_demand_signal_activates_when_supplied() -> None:
    demand = DemandSignal(column="trend", available=True, neutral_value=0.5)
    model = VisibilityModel(demand=demand)
    frame = _frame().with_columns(pl.Series("trend", [0.1] * 10))
    result = model.compute(frame)
    assert result.metadata["demand_component_available"] is True
    assert result.frame["demand_score_norm"].min() == 0.1


def test_rank_groups() -> None:
    frame = VisibilityModel().compute(_frame()).frame
    assert set(frame["rank_group"].unique().to_list()) == {"1-3", "4-6", "7-10"}
    assert set(frame["rank_group_robust"].unique().to_list()) == {"top_half", "bottom_half"}


def test_describe_reports_components() -> None:
    description = VisibilityModel().describe()
    assert description["components"]["demand"]["available"] is False
    assert description["components"]["rank"]["column"] == "serp_position"
