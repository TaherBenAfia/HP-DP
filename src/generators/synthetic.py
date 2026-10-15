"""Deterministic, seed-based synthetic event generator for multi-tenant streaming validation.

Supports reproducible simulation of:
- Multi-tenant distribution
- Clean, conforming domain events
- Deliberately injected duplicate events (matching event_ids)
- Malformed/invalid records (schema violations, missing required keys, boundary errors)
- Temporal jitter and out-of-order events
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any


class SyntheticEventGenerator:
    """Generates deterministic sequences of streaming analytical events."""

    DOMAINS = [
        "sportingnews.com",
        "espn.com",
        "marca.com",
        "skysports.com",
        "lequipe.fr",
        "goal.com",
        "bbc.com",
        "as.com",
    ]

    ENTITIES = [
        "Tunisia Ligue 1",
        "Champions League",
        "World Cup Qualifiers",
        "African Cup of Nations",
        "CAF Champions League",
        "Premier League",
        "La Liga",
    ]

    DEMAND_QUERIES = [
        "tunisia ligue 1 standings",
        "champions league fixtures",
        "esperance tunis live score",
        "club africain match summary",
        "etoile du sahel news",
    ]

    def __init__(
        self,
        seed: int = 42,
        tenants: list[str] | None = None,
        duplicate_rate: float = 0.05,
        malformed_rate: float = 0.05,
        schema_version: str = "1.0.0",
    ) -> None:
        self.seed = seed
        self.tenants = tenants or ["tenant_alpha", "tenant_beta"]
        self.duplicate_rate = duplicate_rate
        self.malformed_rate = malformed_rate
        self.schema_version = schema_version
        self._rng = random.Random(seed)
        self._generated_history: list[dict[str, Any]] = []

    def reset(self) -> None:
        """Reset RNG to initial seed state."""
        self._rng = random.Random(self.seed)
        self._generated_history.clear()

    def generate_single_event(
        self,
        force_duplicate: bool = False,
        force_malformed: bool = False,
        base_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Generate a single deterministic event dictionary."""
        # 1. Handle duplicate emission if requested or probabilistic
        if (force_duplicate or (self._rng.random() < self.duplicate_rate)) and self._generated_history:
            dup_target = self._rng.choice(self._generated_history)
            # Duplicate the envelope with same event_id
            return dict(dup_target)

        event_id = f"evt_{uuid.UUID(int=self._rng.getrandbits(128))}"
        tenant_id = self._rng.choice(self.tenants)
        domain = self._rng.choice(self.DOMAINS)
        entity = self._rng.choice(self.ENTITIES)
        slug = entity.lower().replace(" ", "-")
        url = f"https://{domain}/news/{slug}-{self._rng.randint(100, 999)}"

        now = base_time or datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        time_offset = timedelta(seconds=self._rng.randint(0, 3600))
        event_time = now + time_offset

        # 2. Handle malformed record generation
        if force_malformed or (self._rng.random() < self.malformed_rate):
            malformed_type = self._rng.choice(["missing_url", "negative_rank", "invalid_type", "bad_url_format"])
            if malformed_type == "missing_url":
                payload: dict[str, Any] = {
                    "domain": domain,
                    "rank": 5,
                    "entity": entity,
                    "page_speed_score": 85.0,
                }
            elif malformed_type == "negative_rank":
                payload = {
                    "url": url,
                    "domain": domain,
                    "rank": -10,  # Violates ge=1 constraint
                    "entity": entity,
                    "page_speed_score": 80.0,
                }
            elif malformed_type == "bad_url_format":
                payload = {
                    "url": "not-a-valid-http-url",  # Violates url validation
                    "domain": domain,
                    "rank": 3,
                    "entity": entity,
                    "page_speed_score": 75.0,
                }
            else:
                payload = {
                    "url": url,
                    "domain": domain,
                    "rank": "invalid_string_rank",  # Type error
                    "entity": entity,
                    "page_speed_score": "not_a_float",
                }

            event: dict[str, Any] = {
                "event_id": event_id,
                "tenant_id": tenant_id,
                "source": "sports_seo_crawler",
                "event_type": "page_observation",
                "timestamp": event_time.isoformat(),
                "schema_version": self.schema_version,
                "payload": payload,
            }
            return event

        # 3. Clean, conforming event
        payload = {
            "url": url,
            "domain": domain,
            "rank": self._rng.randint(1, 50),
            "entity": entity,
            "h1_count": self._rng.randint(1, 4),
            "word_count": self._rng.randint(300, 3500),
            "schema_org_present": self._rng.choice([True, False]),
            "page_speed_score": round(self._rng.uniform(35.0, 99.0), 1),
            "demand_query": self._rng.choice(self.DEMAND_QUERIES),
        }

        event = {
            "event_id": event_id,
            "tenant_id": tenant_id,
            "source": "sports_seo_crawler",
            "event_type": "page_observation",
            "timestamp": event_time.isoformat(),
            "schema_version": self.schema_version,
            "payload": payload,
        }

        self._generated_history.append(event)
        return event

    def generate_batch(
        self,
        count: int = 100,
        base_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Generate an ordered batch of synthetic events."""
        events = []
        for _ in range(count):
            events.append(self.generate_single_event(base_time=base_time))
        return events

    def stream(
        self,
        count: int = 100,
        base_time: datetime | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stream synthetic events sequentially."""
        yield from self.generate_batch(count=count, base_time=base_time)
