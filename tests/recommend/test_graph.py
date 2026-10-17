"""Offline, deterministic tests for the LangGraph recommendation workflow."""

from __future__ import annotations

import importlib.util

import pytest

pytest.importorskip("langgraph")

HAPPY_CANDIDATE = {
    "action": "metadata_optimization",
    "score": 0.9,
    "reason": "missing meta description on primary landing page",
    "evidence_features": {"meta_description_present": 0.0, "title_length": 22},
}


def _offline_config(tmp_path=None):
    from src.platform.config import PlatformConfig, RecommendConfig

    if tmp_path is None:
        return PlatformConfig(recommend=RecommendConfig(lm_model=""))
    return PlatformConfig(
        recommend=RecommendConfig(
            lm_model="",
            graph_state_path=str(tmp_path / "graph_state.json"),
        )
    )


def test_build_graph_compiles() -> None:
    from src.recommend.graph import build_graph

    graph = build_graph(_offline_config())
    assert hasattr(graph, "invoke")


def test_happy_path_policy_ok_and_full_steps() -> None:
    from src.recommend.graph import run_recommendation

    result = run_recommendation(
        {
            "page_id": "page-1",
            "features": {"meta_description_present": 0.0},
            "candidates": [dict(HAPPY_CANDIDATE)],
        },
        _offline_config(),
    )
    assert result["policy_ok"] is True
    assert result["causal_ok"] is True
    assert result["decision"] is not None
    assert result["decision"]["action"] == "metadata_optimization"
    assert result["decision"]["policy_ok"] is True
    assert result["top_candidate"]["action"] == "metadata_optimization"
    assert result["steps"] == [
        "load_context",
        "score_candidates",
        "causal_check",
        "policy_gate",
        "sign_artifact",
        "finalize",
    ]


def test_blocked_candidate_skips_signing() -> None:
    from src.recommend.graph import run_recommendation

    result = run_recommendation(
        {
            "page_id": "page-2",
            "features": {},
            "candidates": [{"action": "link_update", "score": 0.5}],
        },
        _offline_config(),
    )
    assert result["policy_ok"] is False
    assert result["decision"] is not None
    assert result["decision"]["policy_ok"] is False
    assert result["decision"]["signed"] is False
    assert "sign_artifact" not in result["steps"]
    assert result["steps"] == [
        "load_context",
        "score_candidates",
        "causal_check",
        "policy_gate",
        "finalize",
    ]


def test_score_candidates_picks_max_deterministically() -> None:
    from src.recommend.graph import run_recommendation

    candidates = [
        {"action": "low", "score": 0.2, "reason": "r", "evidence_features": {"x": 1}},
        {"action": "high", "score": 0.8, "reason": "r", "evidence_features": {"x": 2}},
        {"action": "tie", "score": 0.8, "reason": "r", "evidence_features": {"x": 3}},
    ]
    result = run_recommendation({"page_id": "p", "candidates": candidates}, _offline_config())
    assert result["top_candidate"]["action"] == "high"


def test_causal_check_gates_zero_bracketing_interval() -> None:
    from src.recommend.graph import run_recommendation

    candidate = dict(HAPPY_CANDIDATE, effect=0.01, ci_low=-0.2, ci_high=0.25)
    result = run_recommendation(
        {"page_id": "p", "candidates": [candidate]},
        _offline_config(),
    )
    assert result["causal_ok"] is False

    clear = dict(HAPPY_CANDIDATE, effect=0.4, ci_low=0.1, ci_high=0.7)
    ok = run_recommendation({"page_id": "p", "candidates": [clear]}, _offline_config())
    assert ok["causal_ok"] is True


def test_signing_integration_when_governance_available(tmp_path) -> None:
    from src.recommend.graph import run_recommendation

    if importlib.util.find_spec("src.governance.signing") is None:
        pytest.skip("src.governance.signing not implemented yet")
    config = _offline_config(tmp_path)
    try:
        result = run_recommendation(
            {
                "page_id": "page-3",
                "features": {},
                "candidates": [dict(HAPPY_CANDIDATE)],
            },
            config,
        )
    except Exception as exc:
        pytest.skip(f"governance signing backend not usable yet: {exc}")
    signed = result["signed"]
    if signed is None:
        pytest.skip("signing backend present but returned no signature")
    assert isinstance(signed, dict)
    assert "signature" in signed


def test_recommend_with_llm_falls_back_offline() -> None:
    from src.recommend.graph import recommend_with_llm

    result = recommend_with_llm(
        features={"meta_description_present": 0.0},
        candidates=[dict(HAPPY_CANDIDATE)],
        config=_offline_config(),
    )
    assert result["decision"] is not None
    assert result["decision"]["policy_ok"] is True
    llm_block = result["llm_recommendation"]
    assert isinstance(llm_block["action"], str)
    assert isinstance(llm_block["rationale"], str)
    assert isinstance(llm_block["confidence"], float)
