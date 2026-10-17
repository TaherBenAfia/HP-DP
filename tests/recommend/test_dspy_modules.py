"""Offline, deterministic tests for the DSPy recommendation module."""

from __future__ import annotations

import pytest

pytest.importorskip("dspy")


def _offline_config():
    from src.platform.config import PlatformConfig, RecommendConfig

    return PlatformConfig(recommend=RecommendConfig(lm_model=""))


def test_signature_has_expected_fields_and_flags() -> None:
    from src.recommend.dspy_modules import ActionRecommendation

    assert set(ActionRecommendation.input_fields) == {"features", "candidates"}
    assert set(ActionRecommendation.output_fields) == {"action", "rationale", "confidence"}
    for name in ("features", "candidates"):
        extra = ActionRecommendation.input_fields[name].json_schema_extra or {}
        assert extra["__dspy_field_type"] == "input"
    for name in ("action", "rationale", "confidence"):
        extra = ActionRecommendation.output_fields[name].json_schema_extra or {}
        assert extra["__dspy_field_type"] == "output"


def test_build_lm_returns_offline_dummy_when_model_empty() -> None:
    import dspy
    from dspy.clients.base_lm import BaseLM

    from src.recommend.dspy_modules import build_lm

    lm = build_lm(_offline_config())
    assert isinstance(lm, BaseLM)
    assert isinstance(lm, dspy.utils.dummies.DummyLM)


def test_configure_dspy_returns_true_offline() -> None:
    from src.recommend.dspy_modules import configure_dspy

    assert configure_dspy(_offline_config()) is True


def test_seo_module_predicts_with_dummy_lm() -> None:
    from src.recommend.dspy_modules import SEORecommendationModule, configure_dspy

    configure_dspy(_offline_config())
    module = SEORecommendationModule()
    prediction = module(features="{}", candidates="[]")
    assert isinstance(prediction.action, str)
    assert isinstance(prediction.rationale, str)
    assert float(prediction.confidence) >= 0.0


def test_recommend_returns_expected_dict_offline() -> None:
    from src.recommend.dspy_modules import recommend

    config = _offline_config()
    out = recommend(
        features={"meta_present": 0.0},
        candidates=[{"action": "metadata_optimization", "score": 0.9}],
        config=config,
    )
    assert set(out) == {"action", "rationale", "confidence", "model"}
    assert isinstance(out["action"], str)
    assert isinstance(out["rationale"], str)
    assert isinstance(out["confidence"], float)
    assert out["model"] == "offline-dummy"


def test_recommend_is_deterministic_offline() -> None:
    from src.recommend.dspy_modules import recommend

    config = _offline_config()
    args = (
        {"kw_density": 0.1},
        [{"action": "content_refresh", "score": 0.4, "reason": "thin content"}],
    )
    first = recommend(*args, config=config)
    second = recommend(*args, config=config)
    assert first == second
