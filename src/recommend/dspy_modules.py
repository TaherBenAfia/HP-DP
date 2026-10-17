"""DSPy signature and module for evidence-backed action recommendations.

Every recommendation produced here is a transparent heuristic over the supplied
evidence features: the LM only ranks, labels and explains candidates that were
already scored from observed data. Outputs are never forecasts, never
probability-of-success estimates, and never causal claims; the confidence field
reflects heuristic agreement with the evidence, not predictive certainty.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import dspy
from dspy.utils.dummies import DummyLM

from src.platform.config import PlatformConfig

_OFFLINE_ANSWER: dict[str, Any] = {
    "reasoning": (
        "Offline dummy LM: no model configured, so the deterministic default "
        "heuristic is returned without network access."
    ),
    "action": "heuristic_review",
    "rationale": (
        "No LM configured; deterministic offline default. Review the evidence "
        "features and candidate scores manually before acting."
    ),
    "confidence": 0.5,
}

_OFFLINE_COMPLETION_POOL = 8


class ActionRecommendation(dspy.Signature):
    """Recommend one action from evidence features and scored candidates.

    The recommendation is a transparent heuristic backed by the supplied
    evidence features, not a forecast and not a causal claim. The rationale
    must cite the evidence that justifies the chosen action, and confidence
    reflects heuristic agreement with that evidence rather than predictive
    certainty.
    """

    features: str = dspy.InputField(desc="JSON-encoded evidence features for the page.")
    candidates: str = dspy.InputField(
        desc="JSON-encoded candidate actions with scores, reasons and evidence."
    )
    action: str = dspy.OutputField(desc="Identifier of the recommended candidate action.")
    rationale: str = dspy.OutputField(
        desc="Evidence-citing justification; no causal or predictive claims."
    )
    confidence: float = dspy.OutputField(
        desc="Heuristic confidence in [0, 1]; not a probability of success."
    )


class SEORecommendationModule(dspy.Module):
    """Chain-of-thought predictor wrapping :class:`ActionRecommendation`."""

    def __init__(self) -> None:
        super().__init__()
        self.predict: dspy.ChainOfThought = dspy.ChainOfThought(ActionRecommendation)

    def forward(self, features: str, candidates: str) -> Any:
        """Run the predictor over JSON-encoded inputs."""
        return self.predict(features=features, candidates=candidates)


def build_lm(config: PlatformConfig) -> Any:
    """Return the configured LM, or dspy's deterministic DummyLM when unset.

    An empty ``config.recommend.lm_model`` selects the offline dummy so that
    tests and air-gapped runs never touch the network.
    """
    model = config.recommend.lm_model
    if model:
        return dspy.LM(model)
    return DummyLM([dict(_OFFLINE_ANSWER) for _ in range(_OFFLINE_COMPLETION_POOL)])


def configure_dspy(config: PlatformConfig) -> bool:
    """Configure DSPy globally with the config-selected LM; never needs network."""
    dspy.configure(lm=build_lm(config))
    return True


def recommend(
    features: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
    config: PlatformConfig,
) -> dict[str, Any]:
    """Produce a heuristic action recommendation from evidence features.

    Inputs are JSON-encoded and passed through :class:`SEORecommendationModule`.
    The result is robust to missing prediction fields and, with an empty
    ``lm_model``, runs fully offline on dspy's DummyLM. The returned action and
    rationale are transparent heuristics, not forecasts or causal claims.
    """
    configure_dspy(config)
    module = SEORecommendationModule()
    prediction = module(
        features=json.dumps(features, sort_keys=True, default=str),
        candidates=json.dumps(list(candidates), sort_keys=True, default=str),
    )
    action = str(getattr(prediction, "action", "") or "")
    rationale = str(getattr(prediction, "rationale", "") or "")
    try:
        confidence = float(getattr(prediction, "confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "action": action,
        "rationale": rationale,
        "confidence": confidence,
        "model": config.recommend.lm_model or "offline-dummy",
    }
