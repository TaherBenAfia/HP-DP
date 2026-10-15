"""Action engine tests."""

from __future__ import annotations

import json

import polars as pl
import pytest

from src.analytics.action_engine import (
    ActionFamily,
    DeficitRule,
    compute_deficits,
    score_actions,
)
from src.analytics.feature_registry import FeatureKind, FeatureRegistry, FeatureSpec


def _registry() -> FeatureRegistry:
    return FeatureRegistry(
        [
            FeatureSpec(name="flag", family="fam_a", kind=FeatureKind.BINARY,
                        higher_is_better=True),
            FeatureSpec(name="score", family="fam_a", kind=FeatureKind.NUMERIC,
                        higher_is_better=True),
            FeatureSpec(name="depth", family="fam_b", kind=FeatureKind.NUMERIC,
                        higher_is_better=False),
        ]
    )


def _families() -> list[ActionFamily]:
    return [
        ActionFamily(
            name="fam_a",
            description="family A",
            features=["flag", "score"],
            weights={"flag": 2.0, "score": 1.0},
            rules={"flag": DeficitRule(kind="binary", desired=True)},
            recommendations={"flag": "add the flag"},
            summary="summary A",
        ),
        ActionFamily(
            name="fam_b",
            description="family B",
            features=["depth"],
            weights={"depth": 1.0},
            rules={"depth": DeficitRule(kind="range", low=0.0, high=5.0)},
            recommendations={"depth": "flatten the depth"},
            summary="summary B",
        ),
    ]


def _frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "url": ["https://a.test/1", "https://a.test/2"],
            "query": ["q1", "q1"],
            "entity": ["A", "A"],
            "serp_position": [1, 9],
            "flag": [1, 0],
            "score": [10.0, 1.0],
            "depth": [2.0, 12.0],
        }
    )


def test_binary_deficit() -> None:
    scored = compute_deficits(_frame(), _registry(), _families())
    assert scored["deficit__flag"].to_list() == [0.0, 1.0]


def test_range_deficit_is_zero_inside_band() -> None:
    scored = compute_deficits(_frame(), _registry(), _families())
    deficits = scored["deficit__depth"].to_list()
    assert deficits[0] == 0.0
    assert deficits[1] > 0


def test_constant_feature_is_skipped() -> None:
    frame = _frame().with_columns(pl.lit(3.0).alias("flat"))
    families = [
        ActionFamily(name="fam_flat", description="", features=["flat"]),
    ]
    scored = compute_deficits(frame, _registry(), families)
    assert "deficit__flat" not in scored.columns


def test_scores_stay_in_unit_interval() -> None:
    result = score_actions(_frame(), _families(), _registry())
    for column in ("fam_a_action_score", "fam_b_action_score", "primary_action_score"):
        assert result[column].min() >= 0
        assert result[column].max() <= 1


def test_primary_action_is_the_largest_deficit() -> None:
    result = score_actions(_frame(), _families(), _registry())
    scores = {
        "fam_a": result["fam_a_action_score"][1],
        "fam_b": result["fam_b_action_score"][1],
    }
    expected = max(scores, key=lambda key: scores[key])
    assert result["primary_action"][1] == expected
    assert result["primary_action_score"][1] == pytest.approx(max(scores.values()))


def test_priority_bands() -> None:
    frame = pl.DataFrame(
        {
            "url": ["https://a.test/1", "https://a.test/2", "https://a.test/3"],
            "query": ["q1", "q1", "q1"],
            "entity": ["A", "A", "A"],
            "serp_position": [1, 5, 9],
            "flag": [0, 1, 1],
            "score": [0.0, 5.0, 10.0],
            "depth": [50.0, 1.0, 2.0],
        }
    )
    result = score_actions(frame, _families(), _registry())
    assert result["primary_action_score"][0] >= 0.66
    assert result["action_priority"][0] == "high"
    # a healthy row must rank below the high threshold
    assert result["primary_action_score"][2] < 0.66
    assert result["action_priority"][2] in {"low", "medium"}


def test_evidence_features_are_json() -> None:
    result = score_actions(_frame(), _families(), _registry())
    payload = json.loads(result["evidence_features"][0])
    assert isinstance(payload, list)
    assert payload
    assert {"feature", "deficit", "weight", "support", "q_value"} <= set(payload[0])


def test_reason_and_recommendation_are_populated() -> None:
    result = score_actions(_frame(), _families(), _registry())
    assert "largest deficit" in result["reason"][0]
    assert result["recommended_action"][0]


def test_evidence_reweights_features() -> None:
    evidence = pl.DataFrame(
        {
            "feature": ["flag"],
            "family": ["fam_a"],
            "outcome": ["page_trend"],
            "test": ["chi_square"],
            "n": [100],
            "statistic": [10.0],
            "p_value": [0.001],
            "effect_size": [0.5],
            "effect_size_type": ["cramers_v"],
            "q_value": [0.002],
            "significant_fdr": [True],
            "direction": ["positive"],
            "status": ["ok"],
            "notes": [""],
        }
    )
    without = score_actions(_frame(), _families(), _registry(), evidence=None)
    with_evidence = score_actions(_frame(), _families(), _registry(), evidence=evidence)
    # supported "flag" now carries more weight than the unsupported "score"
    assert with_evidence["fam_a_action_score"][1] != without["fam_a_action_score"][1]
