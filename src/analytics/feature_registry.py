"""Feature registry.

Domains declare their features once; every generic engine (statistics, action
scoring, metrics, dashboards) reads the declaration instead of hard-coding
domain knowledge.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class FeatureRole(StrEnum):
    """Role a column plays in the analytical model."""

    IDENTITY = "identity"
    OUTCOME = "outcome"
    FEATURE = "feature"
    COMPETITOR = "competitor"
    DECISION = "decision"


class FeatureKind(StrEnum):
    """Statistical handling of a feature."""

    NUMERIC = "numeric"
    BINARY = "binary"
    CATEGORICAL = "categorical"
    TEXT = "text"


@dataclass(frozen=True)
class FeatureSpec:
    """Declaration of one analytical feature."""

    name: str
    family: str
    kind: FeatureKind = FeatureKind.NUMERIC
    role: FeatureRole = FeatureRole.FEATURE
    higher_is_better: bool | None = None
    expected_direction: str = "positive"
    description: str = ""
    available: bool = True

    @property
    def testable(self) -> bool:
        """Whether the feature can enter the statistical engine."""
        return (
            self.available
            and self.kind in {FeatureKind.NUMERIC, FeatureKind.BINARY, FeatureKind.CATEGORICAL}
            and self.role in {FeatureRole.FEATURE, FeatureRole.COMPETITOR, FeatureRole.OUTCOME}
        )


class FeatureRegistry:
    """Ordered registry with family lookups."""

    def __init__(self, specs: Iterable[FeatureSpec] = ()) -> None:
        self._specs: dict[str, FeatureSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        """Register (or replace) a feature declaration."""
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> FeatureSpec | None:
        """Look up a single feature."""
        return self._specs.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)

    @property
    def all(self) -> list[FeatureSpec]:
        """Every registered feature."""
        return list(self._specs.values())

    def by_family(self, family: str) -> list[FeatureSpec]:
        """Features of one family."""
        return [spec for spec in self._specs.values() if spec.family == family]

    def families(self) -> list[str]:
        """Distinct families in registration order."""
        seen: list[str] = []
        for spec in self._specs.values():
            if spec.family not in seen:
                seen.append(spec.family)
        return seen

    def roles(self, role: FeatureRole) -> list[FeatureSpec]:
        """Features with a given role."""
        return [spec for spec in self._specs.values() if spec.role == role]

    def testable(self) -> list[FeatureSpec]:
        """Features the statistical engine may evaluate."""
        return [spec for spec in self._specs.values() if spec.testable]

    def names(self, specs: list[FeatureSpec] | None = None) -> list[str]:
        """Names of the given features (or of all features)."""
        return [spec.name for spec in (self.all if specs is None else specs)]

    def summary(self) -> dict[str, int]:
        """Counts by family for documentation and CLI output."""
        counts: dict[str, int] = {}
        for spec in self._specs.values():
            counts[spec.family] = counts.get(spec.family, 0) + 1
        return counts


@dataclass
class FeatureSet:
    """A named collection of features with weights, used by action families."""

    name: str
    features: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)

    def weight(self, feature: str) -> float:
        """Base weight of a feature inside this set (default 1.0)."""
        return float(self.weights.get(feature, 1.0))
