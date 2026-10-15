"""Unit tests for the deterministic synthetic event generator."""

from __future__ import annotations

from src.generators.synthetic import SyntheticEventGenerator


def test_generator_determinism() -> None:
    """Same seed must produce bit-for-bit identical batches of events."""
    gen1 = SyntheticEventGenerator(seed=12345, duplicate_rate=0.0, malformed_rate=0.0)
    batch1 = gen1.generate_batch(count=20)

    gen2 = SyntheticEventGenerator(seed=12345, duplicate_rate=0.0, malformed_rate=0.0)
    batch2 = gen2.generate_batch(count=20)

    assert batch1 == batch2


def test_generator_different_seeds() -> None:
    """Different seeds must produce different sequences."""
    gen1 = SyntheticEventGenerator(seed=100, duplicate_rate=0.0, malformed_rate=0.0)
    gen2 = SyntheticEventGenerator(seed=200, duplicate_rate=0.0, malformed_rate=0.0)

    batch1 = gen1.generate_batch(count=10)
    batch2 = gen2.generate_batch(count=10)

    assert batch1 != batch2


def test_generator_multi_tenant_distribution() -> None:
    """Events must be partitioned across the specified tenants."""
    tenants = ["tenant_x", "tenant_y", "tenant_z"]
    gen = SyntheticEventGenerator(seed=42, tenants=tenants, duplicate_rate=0.0, malformed_rate=0.0)
    batch = gen.generate_batch(count=50)

    found_tenants = {evt["tenant_id"] for evt in batch}
    assert found_tenants == set(tenants)


def test_generator_duplicate_injection() -> None:
    """With duplicate_rate > 0, repeated event_ids must be emitted."""
    gen = SyntheticEventGenerator(seed=999, duplicate_rate=0.5, malformed_rate=0.0)
    batch = gen.generate_batch(count=50)

    event_ids = [evt["event_id"] for evt in batch]
    assert len(event_ids) > len(set(event_ids)), "Expected duplicates in batch"


def test_generator_forced_malformed_event() -> None:
    """Force-generated malformed events must exhibit intentional schema defects."""
    gen = SyntheticEventGenerator(seed=42)
    event = gen.generate_single_event(force_malformed=True)

    payload = event["payload"]
    is_bad = (
        ("url" not in payload)
        or (isinstance(payload.get("rank"), int) and payload["rank"] < 1)
        or (not isinstance(payload.get("rank"), int))
        or (isinstance(payload.get("url"), str) and not payload["url"].startswith("http"))
    )
    assert is_bad, f"Malformed event had valid payload: {payload}"
