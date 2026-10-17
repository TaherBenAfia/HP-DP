"""Policy-as-code evaluation for the governance layer.

Policies are declared declaratively in ``configs/policies.yaml`` and bound to
implemented rules here. Each rule receives the platform configuration and the
policy params, and reports whether the policy passes along with a message.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import yaml
from pydantic import BaseModel, Field

from src.platform.config import PlatformConfig, load_platform_config

_MUTATING_OPERATIONS = {"DELETE", "UPDATE", "MERGE"}
Params = dict[str, Any]


class Policy(BaseModel):
    """A single declarative policy loaded from ``policies.yaml``."""

    id: str
    description: str = ""
    rule: str = ""
    severity: str = "error"
    params: Params = Field(default_factory=dict)


class PolicyResult(BaseModel):
    """The outcome of evaluating one policy."""

    policy_id: str
    severity: str
    passed: bool
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class GovernanceReport(BaseModel):
    """Aggregated result of evaluating every loaded policy."""

    passed: bool
    results: list[PolicyResult] = Field(default_factory=list)
    errors: list[PolicyResult] = Field(default_factory=list)
    warnings: list[PolicyResult] = Field(default_factory=list)

    def render(self) -> str:
        """Return a human-readable summary of the report."""
        status = "PASS" if self.passed else "FAIL"
        lines = [f"Governance report: {status}"]
        for result in self.results:
            mark = "ok" if result.passed else "FAIL"
            lines.append(f"  [{mark}] {result.policy_id} ({result.severity}): {result.message}")
        lines.append(f"  errors={len(self.errors)} warnings={len(self.warnings)}")
        return "\n".join(lines)


class PolicyViolation(RuntimeError):
    """Raised when enforcing policies fails with at least one error-severity result."""

    def __init__(self, report: GovernanceReport) -> None:
        super().__init__(report.render())
        self.report = report


def load_policies(config: PlatformConfig) -> list[Policy]:
    """Parse ``config.governance.policies_path``; tolerate a missing file."""
    path = config.resolve(config.governance.policies_path)
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("policies") or []
    policies: list[Policy] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        policies.append(
            Policy(
                id=str(entry.get("id", "")),
                description=str(entry.get("description", "")),
                rule=str(entry.get("rule", "")),
                severity=str(entry.get("severity", "error")),
                params=dict(entry.get("params") or {}),
            )
        )
    return policies


def _rule_bronze_append_only(config: PlatformConfig, params: Params) -> tuple[bool, str]:
    from src.platform.storage import Storage

    storage = Storage(config)
    bronze_dir = config.resolve(config.paths.bronze_dir)
    if not bronze_dir.exists():
        return True, "no bronze directory; append-only requirement satisfied"
    domains = [
        child.name
        for child in bronze_dir.iterdir()
        if child.is_dir() and not child.name.startswith("_")
    ]
    if not domains:
        return True, "no bronze tables; append-only requirement satisfied"

    violations: list[str] = []
    for domain in domains:
        table = storage.bronze_table(domain)
        for entry in storage.history(table, limit=10000):
            operation = str(entry.get("operation", "")).upper()
            if operation in _MUTATING_OPERATIONS:
                violations.append(f"{domain}:{operation}")
    if violations:
        return False, "mutating operations on bronze: " + ", ".join(violations)
    return True, "bronze tables are append-only"


def _rule_quarantine_not_dropped(config: PlatformConfig, params: Params) -> tuple[bool, str]:
    if config.silver.quarantine_invalid_rows is True:
        return True, "invalid rows are quarantined, not dropped"
    return False, "quarantine_invalid_rows is disabled; invalid rows may be dropped"


def _rule_fdr_alpha_max(config: PlatformConfig, params: Params) -> tuple[bool, str]:
    max_alpha = float(params.get("max_alpha", 0.05))
    alpha = float(config.statistics.fdr_alpha)
    if alpha <= max_alpha:
        return True, f"alpha {alpha} within approved bound {max_alpha}"
    return False, f"alpha {alpha} exceeds approved bound {max_alpha}"


def _rule_interpretation_associational(config: PlatformConfig, params: Params) -> tuple[bool, str]:
    if config.statistics.interpretation == "associational":
        return True, "statistics remain associational"
    return False, f"interpretation is '{config.statistics.interpretation}', not associational"


def _rule_gold_lineage_present(config: PlatformConfig, params: Params) -> tuple[bool, str]:
    from src.platform.lineage import LineageStore
    from src.platform.storage import Storage
    from src.query.engine import DEFAULT_VIEWS

    storage = Storage(config)
    existing = [
        view for view in DEFAULT_VIEWS if storage.exists(storage.gold_table(view))
    ]
    if not existing:
        return True, "no gold tables exist; lineage requirement satisfied"

    records = LineageStore(config).records()
    if records is None or records.height == 0:
        return False, "lineage store empty but gold tables exist: " + ", ".join(existing)

    referenced = {
        str(table).rsplit("/", 1)[-1]
        for table in records["table"].to_list()
    } if "table" in records.columns else set()
    missing = [
        product for product in existing if product != "lineage" and product not in referenced
    ]
    if missing:
        return False, "gold tables without lineage records: " + ", ".join(missing)
    return True, "all gold tables have lineage records"


def _rule_artifacts_signed(config: PlatformConfig, params: Params) -> tuple[bool, str]:
    artifact = params.get("artifact")
    if artifact:
        manifest = config.resolve(str(artifact))
        candidates = [manifest] if manifest.exists() else []
    else:
        manifests_dir = config.resolve(config.paths.sharing_manifest_dir)
        if manifests_dir.exists():
            candidates = [
                path
                for path in manifests_dir.rglob("*.json")
                if path.is_file()
                and "recommendation" in path.name.lower()
                and not path.name.endswith(".signed.json")
            ]
        else:
            candidates = []

    if not candidates:
        return True, "no recommendation manifest found; nothing to verify"

    missing = []
    for manifest in candidates:
        signed = manifest.with_name(manifest.stem + ".signed.json")
        if not signed.exists():
            missing.append(str(signed))
    if missing:
        return False, "missing signature files: " + ", ".join(missing)
    return True, f"all {len(candidates)} recommendation manifest(s) signed"


def _rule_demand_component_neutral(config: PlatformConfig, params: Params) -> tuple[bool, str]:
    neutral = config.visibility.demand_component_available is False
    weight = config.visibility.weights.get("demand", 1)
    if neutral and float(weight) == 0.0:
        return True, "demand component is neutral (unavailable, zero weight)"
    return False, (
        f"demand_component_available={config.visibility.demand_component_available}, "
        f"demand weight={weight}"
    )


_RULE_REGISTRY: dict[str, Callable[[PlatformConfig, Params], tuple[bool, str]]] = {
    "bronze_append_only": _rule_bronze_append_only,
    "quarantine_not_dropped": _rule_quarantine_not_dropped,
    "fdr_alpha_max": _rule_fdr_alpha_max,
    "interpretation_associational": _rule_interpretation_associational,
    "gold_lineage_present": _rule_gold_lineage_present,
    "artifacts_signed": _rule_artifacts_signed,
    "demand_component_neutral": _rule_demand_component_neutral,
}


def evaluate_policies(config: PlatformConfig) -> list[PolicyResult]:
    """Run every loaded policy and return one ``PolicyResult`` per policy."""
    policies = load_policies(config)
    results: list[PolicyResult] = []
    for policy in policies:
        rule = _RULE_REGISTRY.get(policy.rule)
        if rule is None:
            results.append(
                PolicyResult(
                    policy_id=policy.id,
                    severity=policy.severity,
                    passed=False,
                    message=f"unknown rule: {policy.rule}",
                )
            )
            continue
        try:
            passed, message = rule(config, policy.params)
        except Exception as exc:
            results.append(
                PolicyResult(
                    policy_id=policy.id,
                    severity=policy.severity,
                    passed=False,
                    message=f"rule execution error: {exc}",
                )
            )
            continue
        results.append(
            PolicyResult(
                policy_id=policy.id,
                severity=policy.severity,
                passed=bool(passed),
                message=message,
            )
        )
    return results


def enforce(config: PlatformConfig | None = None, raise_on_error: bool = False) -> GovernanceReport:
    """Evaluate policies and optionally raise ``PolicyViolation`` on error failures."""
    cfg = config if config is not None else load_platform_config()
    results = evaluate_policies(cfg)
    errors = [r for r in results if r.severity == "error" and not r.passed]
    warnings = [r for r in results if r.severity == "warning" and not r.passed]
    report = GovernanceReport(
        passed=not errors,
        results=results,
        errors=errors,
        warnings=warnings,
    )
    if raise_on_error and report.errors:
        raise PolicyViolation(report)
    return report
