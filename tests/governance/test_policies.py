"""Tests for policy-as-code evaluation and enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.governance.policies import PolicyViolation, enforce, evaluate_policies
from src.platform.config import (
    GovernanceConfig,
    PlatformConfig,
    StatisticsConfig,
    VisibilityConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICIES_PATH = REPO_ROOT / "configs" / "policies.yaml"


def _config(
    tmp_path: Path,
    fdr_alpha: float = 0.05,
    interpretation: str = "associational",
    weights: dict[str, float] | None = None,
) -> PlatformConfig:
    return PlatformConfig(
        repo_root=tmp_path,
        governance=GovernanceConfig(
            policies_path=str(POLICIES_PATH),
            signing_key_path="keys/k.key",
        ),
        statistics=StatisticsConfig(
            fdr_alpha=fdr_alpha, interpretation=interpretation
        ),
        visibility=VisibilityConfig(
            demand_component_available=False,
            weights=weights or {"rank": 0.5, "entity": 0.5, "demand": 0.0},
        ),
    )


def test_defaults_all_policies_pass(tmp_path: Path) -> None:
    report = enforce(_config(tmp_path))
    assert report.passed is True
    assert len(report.results) == 7
    assert all(result.passed for result in report.results)
    assert report.errors == []
    assert report.render()


def test_fdr_alpha_above_bound_fails(tmp_path: Path) -> None:
    report = enforce(_config(tmp_path, fdr_alpha=0.5))
    assert report.passed is False
    assert any(
        result.policy_id == "fdr_discipline" and not result.passed
        for result in report.results
    )


def test_interpretation_causal_fails(tmp_path: Path) -> None:
    report = enforce(_config(tmp_path, interpretation="causal"))
    assert report.passed is False
    assert any(
        result.policy_id == "no_causal_claims" and not result.passed
        for result in report.results
    )


def test_demand_weights_non_neutral_fails(tmp_path: Path) -> None:
    weights = {"rank": 0.5, "entity": 0.5, "demand": 1.0}
    report = enforce(_config(tmp_path, weights=weights))
    assert report.passed is False
    assert any(
        result.policy_id == "demand_component_neutral" and not result.passed
        for result in report.results
    )


def test_enforce_raises_policy_violation(tmp_path: Path) -> None:
    with pytest.raises(PolicyViolation) as excinfo:
        enforce(_config(tmp_path, fdr_alpha=0.5), raise_on_error=True)
    assert excinfo.value.report.errors
    assert excinfo.value.report.passed is False


def test_unknown_rule_fails(tmp_path: Path) -> None:
    policies_file = tmp_path / "configs" / "policies.yaml"
    policies_file.parent.mkdir(parents=True, exist_ok=True)
    policies_file.write_text(
        "policies:\n"
        "  - id: mystery\n"
        "    description: unknown\n"
        "    rule: does_not_exist\n"
        "    severity: error\n",
        encoding="utf-8",
    )
    config = PlatformConfig(
        repo_root=tmp_path,
        governance=GovernanceConfig(policies_path="configs/policies.yaml"),
    )
    results = evaluate_policies(config)
    assert len(results) == 1
    assert results[0].passed is False
    assert "unknown rule" in results[0].message
