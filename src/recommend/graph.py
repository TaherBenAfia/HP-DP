"""LangGraph workflow for evidence-gated, optionally signed recommendations.

The graph ranks candidate actions by observed evidence scores and applies a
causal-sanity gate plus a lightweight transparency (policy) gate before a
decision is assembled and, when governance signing is available, signed.
Decisions are transparent heuristics over observed features: they are never
presented as forecasts and never as causal claims.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from src.platform.config import PlatformConfig, load_platform_config


class RecommendationState(TypedDict, total=False):
    """Mutable state flowing through the recommendation graph."""

    page_id: str
    features: dict[str, Any]
    candidates: list[dict[str, Any]]
    top_candidate: dict[str, Any] | None
    causal_ok: bool
    policy_ok: bool
    signed: dict[str, Any] | None
    decision: dict[str, Any] | None
    steps: list[str]


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _signing_key_path(config: PlatformConfig) -> Path:
    state_path = config.resolve(config.recommend.graph_state_path)
    return state_path.parent / "recommend_artifact_signing.key"


def load_context(state: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize raw inputs into a well-typed graph state."""
    raw_features = state.get("features")
    features = dict(raw_features) if isinstance(raw_features, Mapping) else {}
    raw_candidates = state.get("candidates")
    if isinstance(raw_candidates, list):
        candidates = [dict(item) for item in raw_candidates if isinstance(item, Mapping)]
    else:
        candidates = []
    return {
        "page_id": str(state.get("page_id") or ""),
        "features": features,
        "candidates": candidates,
        "steps": ["load_context"],
    }


def score_candidates(state: Mapping[str, Any]) -> dict[str, Any]:
    """Pick the highest-scoring candidate; ties resolve to the first occurrence."""
    steps = [*state.get("steps", []), "score_candidates"]
    top: dict[str, Any] | None = None
    best = float("-inf")
    for candidate in state.get("candidates") or []:
        score = _as_float(candidate.get("score", 0.0), 0.0)
        if top is None or score > best:
            top = dict(candidate)
            best = score
    return {"top_candidate": top, "steps": steps}


def _maybe_refine_interval(top: Mapping[str, Any]) -> tuple[float, float] | None:
    inputs = top.get("conformal_inputs")
    if not isinstance(inputs, Mapping):
        return None
    try:
        from src.causal.conformal import split_conformal_intervals

        result = split_conformal_intervals(**dict(inputs))
    except Exception:
        return None
    if isinstance(result, Mapping):
        low, high = result.get("ci_low"), result.get("ci_high")
    else:
        low = getattr(result, "ci_low", None)
        high = getattr(result, "ci_high", None)
    if low is None or high is None:
        return None
    return _as_float(low, 0.0), _as_float(high, 0.0)


def _evaluate_causal_ok(top: Mapping[str, Any]) -> bool:
    low_raw, high_raw = top.get("ci_low"), top.get("ci_high")
    if low_raw is None or high_raw is None:
        interval = _maybe_refine_interval(top)
        if interval is None:
            return True
        low, high = interval
    else:
        low, high = _as_float(low_raw, 0.0), _as_float(high_raw, 0.0)
    effect = _as_float(top.get("effect", top.get("effect_estimate", 0.0)), 0.0)
    lo, hi = (low, high) if low <= high else (high, low)
    if lo <= 0.0 <= hi:
        return False
    return effect != 0.0


def causal_check(state: Mapping[str, Any]) -> dict[str, Any]:
    """Sanity-check the top candidate's evidence intervals; never raises.

    A candidate passes when it carries no confidence-interval fields (a pure
    ranking heuristic) or when its interval excludes zero around a non-zero
    effect estimate, i.e. the evidence supports a non-zero effect. This is a
    sanity gate on how evidence is presented, not a causal claim; the
    conformal module is imported lazily only when the candidate supplies
    conformal inputs to refine a missing interval.
    """
    steps = [*state.get("steps", []), "causal_check"]
    top = state.get("top_candidate") or {}
    try:
        causal_ok = _evaluate_causal_ok(top)
    except Exception:
        causal_ok = True
    return {"causal_ok": causal_ok, "steps": steps}


def policy_gate(state: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the lightweight transparency gate to the top candidate.

    A candidate passes only when it carries a non-empty reason/rationale and
    non-empty evidence features, so every accepted recommendation is auditable
    against observed evidence. Governance policy evaluation
    (``src.governance.policies``) can be layered on by callers; the default
    gate stays dependency-free and robust.
    """
    steps = [*state.get("steps", []), "policy_gate"]
    top = state.get("top_candidate") or {}
    rationale = top.get("reason") or top.get("rationale") or ""
    evidence = top.get("evidence_features") or top.get("evidence") or {}
    policy_ok = bool(str(rationale).strip()) and bool(evidence)
    return {"policy_ok": policy_ok, "steps": steps}


def sign_artifact(state: Mapping[str, Any], config: PlatformConfig) -> dict[str, Any]:
    """Sign the decision payload when the governance signer is available.

    The key lives under a path derived from ``recommend.graph_state_path``.
    Any import or signing failure degrades to ``signed=None`` so the workflow
    never blocks on an unavailable signing backend.
    """
    steps = [*state.get("steps", []), "sign_artifact"]
    top = state.get("top_candidate") or {}
    payload = {
        "page_id": str(state.get("page_id", "")),
        "action": str(top.get("action", "")),
        "score": _as_float(top.get("score"), 0.0),
        "rationale": str(top.get("reason") or top.get("rationale") or ""),
    }
    signed: dict[str, Any] | None = None
    try:
        from src.governance.signing import load_or_create_keypair, sign_payload

        private_pem, _public_pem = load_or_create_keypair(_signing_key_path(config))
        result = sign_payload(payload, private_pem)
        signed = dict(result) if isinstance(result, Mapping) else None
    except Exception:
        signed = None
    return {"signed": signed, "steps": steps}


def finalize(state: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble the final decision, including blocked outcomes, from the state."""
    steps = [*state.get("steps", []), "finalize"]
    top = state.get("top_candidate") or {}
    decision = {
        "page_id": str(state.get("page_id", "")),
        "action": str(top.get("action", "")),
        "score": _as_float(top.get("score"), 0.0),
        "rationale": str(top.get("reason") or top.get("rationale") or ""),
        "causal_ok": bool(state.get("causal_ok", False)),
        "policy_ok": bool(state.get("policy_ok", False)),
        "signed": bool(state.get("signed")),
    }
    return {"decision": decision, "steps": steps}


def build_graph(config: PlatformConfig | None = None) -> Any:
    """Compile the recommendation graph: score -> causal -> policy -> sign? -> decide."""
    cfg = config if config is not None else load_platform_config()
    builder = StateGraph(RecommendationState)
    builder.add_node("load_context", load_context)
    builder.add_node("score_candidates", score_candidates)
    builder.add_node("causal_check", causal_check)
    builder.add_node("policy_gate", policy_gate)
    builder.add_node("sign_artifact", lambda state: sign_artifact(state, cfg))
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "score_candidates")
    builder.add_edge("score_candidates", "causal_check")
    builder.add_edge("causal_check", "policy_gate")
    builder.add_conditional_edges(
        "policy_gate",
        lambda state: "sign_artifact" if state.get("policy_ok") else "finalize",
        ["sign_artifact", "finalize"],
    )
    builder.add_edge("sign_artifact", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()


def run_recommendation(
    state: Mapping[str, Any], config: PlatformConfig | None = None
) -> dict[str, Any]:
    """Normalize inputs via ``load_context``, run the graph, return final state."""
    initial = load_context(state)
    graph = build_graph(config)
    result = graph.invoke(initial)
    return dict(result)


def recommend_with_llm(
    features: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
    config: PlatformConfig | None = None,
) -> dict[str, Any]:
    """Enrich the graph decision with a DSPy heuristic; fall back on any error.

    The LLM (or offline dummy) only supplies an action label, rationale and
    heuristic confidence for the already-scored top candidate. On import or
    inference failure the top candidate's own fields are used, so the output
    remains a transparent heuristic and never a forecast or causal claim.
    """
    cfg = config if config is not None else load_platform_config()
    state = run_recommendation(
        {"features": dict(features), "candidates": [dict(item) for item in candidates]},
        cfg,
    )
    top = state.get("top_candidate") or {}
    llm_block: dict[str, Any] = {
        "action": str(top.get("action", "")),
        "rationale": str(top.get("reason") or top.get("rationale") or ""),
        "confidence": _as_float(top.get("score"), 0.0),
        "model": "fallback-top-candidate",
    }
    with suppress(Exception):
        from src.recommend.dspy_modules import recommend as dspy_recommend

        llm_block = dspy_recommend(features, candidates, cfg)
    decision = dict(state.get("decision") or {})
    if decision:
        decision["action"] = llm_block.get("action") or decision.get("action", "")
        decision["rationale"] = llm_block.get("rationale") or decision.get("rationale", "")
        decision["confidence"] = _as_float(llm_block.get("confidence"), 0.0)
    enriched = dict(state)
    enriched["decision"] = decision or None
    enriched["llm_recommendation"] = llm_block
    return enriched
