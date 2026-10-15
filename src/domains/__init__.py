"""Domain plugins."""

from __future__ import annotations

from src.domains.base import DomainAdapter, available_domains, domain_for_dataset, load_domain

__all__ = ["DomainAdapter", "available_domains", "domain_for_dataset", "load_domain"]
