"""Sports SEO domain plugin."""

from __future__ import annotations

from src.domains.sports_seo.actions import FAMILIES
from src.domains.sports_seo.domain import ADAPTER, SportsSEODomain
from src.domains.sports_seo.features import FEATURE_REGISTRY

__all__ = ["ADAPTER", "SportsSEODomain", "FAMILIES", "FEATURE_REGISTRY"]
