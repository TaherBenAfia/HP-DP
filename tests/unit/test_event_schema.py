"""Unit tests for event schema contracts, validation, and evolution."""

from __future__ import annotations

from datetime import UTC, datetime

from src.ingestion.event_schema import (
    SportsSeoObservationPayloadV1_1,
    export_json_schema,
    validate_event,
)


def test_valid_event_validation() -> None:
    """Conforming event must validate cleanly."""
    event = {
        "event_id": "evt_test_101",
        "tenant_id": "tenant_alpha",
        "source": "sports_seo_crawler",
        "event_type": "page_observation",
        "timestamp": datetime.now(UTC).isoformat(),
        "schema_version": "1.0.0",
        "payload": {
            "url": "https://sportingnews.com/news/tunisia-101",
            "domain": "sportingnews.com",
            "rank": 3,
            "entity": "Tunisia Ligue 1",
            "h1_count": 1,
            "word_count": 850,
            "schema_org_present": True,
            "page_speed_score": 88.5,
            "demand_query": "tunisia ligue 1 standings",
        },
    }
    outcome = validate_event(event)
    assert outcome.is_valid is True
    assert outcome.envelope is not None
    assert outcome.envelope.tenant_id == "tenant_alpha"
    assert outcome.validated_payload is not None
    assert outcome.validated_payload["rank"] == 3


def test_invalid_envelope_fields() -> None:
    """Missing or empty tenant_id must produce actionable diagnostics."""
    event = {
        "event_id": "evt_123",
        "tenant_id": "   ",  # blank tenant
        "source": "crawler",
        "event_type": "page_observation",
        "schema_version": "1.0.0",
        "payload": {},
    }
    outcome = validate_event(event)
    assert outcome.is_valid is False
    assert any("tenant_id" in err for err in outcome.error_messages)


def test_invalid_payload_constraints() -> None:
    """Semantic constraint violations must be identified in diagnostics."""
    event = {
        "event_id": "evt_bad_payload",
        "tenant_id": "tenant_alpha",
        "source": "crawler",
        "event_type": "page_observation",
        "schema_version": "1.0.0",
        "payload": {
            "url": "ftp://invalid-protocol.com",
            "domain": "invalid-protocol.com",
            "rank": -5,  # Invalid: ge=1 constraint
            "entity": "Soccer",
            "page_speed_score": 150.0,  # Invalid: le=100.0 constraint
        },
    }
    outcome = validate_event(event)
    assert outcome.is_valid is False
    assert len(outcome.error_messages) >= 2


def test_json_schema_export() -> None:
    """JSON Schema export must produce standard JSON Schema with definitions."""
    schema = export_json_schema("page_observation:1.0.0")
    assert "properties" in schema
    assert "url" in schema["properties"]
    assert "rank" in schema["properties"]
    assert "required" in schema
    assert "url" in schema["required"]
    assert "rank" in schema["required"]


def test_backward_compatible_schema_evolution() -> None:
    """v1.1 payload with new optional fields must work with defaults and explicit values."""
    # 1. Base v1.0 payload validated under v1.1 accepts existing fields with defaults
    base_data = {
        "url": "https://espn.com/news/uefa-1",
        "domain": "espn.com",
        "rank": 1,
        "entity": "Champions League",
        "page_speed_score": 90.0,
    }
    evolved = SportsSeoObservationPayloadV1_1.model_validate(base_data)
    assert evolved.author_count == 1  # Default value
    assert evolved.mobile_friendly is True  # Default value

    # 2. Explicit v1.1 fields provided
    evolved_explicit = SportsSeoObservationPayloadV1_1.model_validate(
        {**base_data, "author_count": 3, "mobile_friendly": False}
    )
    assert evolved_explicit.author_count == 3
    assert evolved_explicit.mobile_friendly is False
