"""Domain plugin interface.

A domain adapter supplies:

1. a datasource contract (already declarative, in ``configs/datasources``);
2. transformation configuration (Silver rules);
3. domain analytical features;
4. domain statistical analysis;
5. domain action/decision logic;

and reuses the platform's ingestion, storage, lineage, sharing and dashboard
infrastructure unchanged.
"""

from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import polars as pl

from src.analytics.base_model import AnalysisKey
from src.analytics.feature_registry import FeatureRegistry
from src.ingestion.contracts import DatasourceContract
from src.platform.config import PlatformConfig, load_platform_config
from src.platform.datasource_registry import DatasourceRegistry


@dataclass
class DomainContext:
    """Everything a domain adapter needs from the platform."""

    config: PlatformConfig
    registry: DatasourceRegistry
    dataset_id: str

    @property
    def contract(self) -> DatasourceContract:
        """The datasource contract for the configured dataset."""
        return self.registry.get(self.dataset_id)


class DomainAdapter(ABC):
    """Contract every domain implementation must fulfil."""

    domain: str = ""
    domain_version: str = "1.0.0"

    def __init__(self, context: DomainContext) -> None:
        self.context = context

    @property
    def config(self) -> PlatformConfig:
        """Platform configuration."""
        return self.context.config

    @property
    def contract(self) -> DatasourceContract:
        """Datasource contract of the active dataset."""
        return self.context.contract

    @property
    @abstractmethod
    def key(self) -> AnalysisKey:
        """Column mapping onto the generic analytical model."""

    @property
    @abstractmethod
    def features(self) -> FeatureRegistry:
        """Domain feature declarations."""

    @abstractmethod
    def build_features(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Add domain analytical features (visibility, groups, scores)."""

    @abstractmethod
    def build_metrics(self, frame: pl.DataFrame) -> dict[str, pl.DataFrame]:
        """Produce Gold metric tables."""

    @abstractmethod
    def run_statistics(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Produce the statistical evidence table."""

    @abstractmethod
    def build_actions(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Produce transparent action recommendations."""

    def describe(self) -> dict[str, Any]:
        """Domain description used by the CLI."""
        return {
            "domain": self.domain,
            "version": self.domain_version,
            "dataset_id": self.context.dataset_id,
            "features": len(self.features),
            "families": self.features.summary(),
        }


_DOMAINS: dict[str, str] = {
    "sports_seo": "src.domains.sports_seo",
}


def available_domains() -> list[str]:
    """Registered domain plugin names."""
    return sorted(_DOMAINS)


def load_domain(
    domain: str,
    dataset_id: str,
    config: PlatformConfig | None = None,
    registry: DatasourceRegistry | None = None,
) -> DomainAdapter:
    """Instantiate a domain adapter by name."""
    if domain not in _DOMAINS:
        raise KeyError(f"unknown domain {domain!r}; available: {available_domains()}")
    cfg = config or load_platform_config()
    reg = registry or DatasourceRegistry(cfg)
    module = importlib.import_module(_DOMAINS[domain])
    adapter_cls = module.ADAPTER
    return adapter_cls(DomainContext(config=cfg, registry=reg, dataset_id=dataset_id))


def domain_for_dataset(
    dataset_id: str,
    config: PlatformConfig | None = None,
    registry: DatasourceRegistry | None = None,
) -> DomainAdapter:
    """Resolve the domain adapter from a dataset's contract."""
    cfg = config or load_platform_config()
    reg = registry or DatasourceRegistry(cfg)
    contract = reg.get(dataset_id)
    return load_domain(contract.domain, dataset_id, cfg, reg)
