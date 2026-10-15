"""Event schema contracts and validation for streaming and batch event ingestion.

All streaming events conform to a typed envelope containing tenant scope, stable
event identity, source metadata, timestamp, schema version, and payload.

Supports portable JSON Schema export and Pydantic runtime validation with
actionable diagnostic error reporting.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class EventEnvelope(BaseModel):
    """Canonical event envelope wrapping any domain-specific payload."""

    event_id: str = Field(..., description="Stable logical identifier for deduplication")
    tenant_id: str = Field(..., description="Tenant isolation boundary")
    source: str = Field(..., description="Data provider or emitter system name")
    event_type: str = Field(..., description="Semantic event category")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Event generation timestamp",
    )
    schema_version: str = Field("1.0.0", description="Contract version of payload")
    payload: dict[str, Any] = Field(..., description="Raw domain event attributes")

    model_config = ConfigDict(extra="forbid")

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("tenant_id cannot be blank")
        return v.strip().lower()

    @field_validator("event_id")
    @classmethod
    def _validate_event_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("event_id cannot be blank")
        return v.strip()


class SportsSeoObservationPayload(BaseModel):
    """Payload contract for sports SEO search observation events (v1.0.0)."""

    url: str = Field(..., min_length=4, description="Target URL")
    domain: str = Field(..., min_length=2, description="Normalized domain")
    rank: int = Field(..., ge=1, le=1000, description="Observed search ranking position (1-based)")
    entity: str = Field(..., min_length=1, description="Primary entity or topic")
    h1_count: int = Field(0, ge=0, description="H1 heading occurrences")
    word_count: int = Field(0, ge=0, description="Page body word count")
    schema_org_present: bool = Field(False, description="Whether Schema.org structured data exists")
    page_speed_score: float = Field(..., ge=0.0, le=100.0, description="Core Web Vitals proxy score 0-100")
    demand_query: str | None = Field(None, description="Optional associated search keyword")

    model_config = ConfigDict(extra="ignore")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must begin with http:// or https://")
        return v


class SportsSeoObservationPayloadV1_1(SportsSeoObservationPayload):
    """Evolved payload contract (v1.1.0) with backward-compatible optional fields."""

    author_count: int = Field(1, ge=0, description="Detected page byline count")
    mobile_friendly: bool = Field(True, description="Viewport mobile optimization indicator")


# Schema Registry mapping
SCHEMA_REGISTRY: dict[str, Type[BaseModel]] = {
    "page_observation:1.0.0": SportsSeoObservationPayload,
    "page_observation:1.1.0": SportsSeoObservationPayloadV1_1,
}


class ValidationOutcome(BaseModel):
    """Result of validating an event against its schema contract."""

    is_valid: bool
    envelope: EventEnvelope | None = None
    validated_payload: dict[str, Any] | None = None
    error_messages: list[str] = Field(default_factory=list)


def validate_event(raw_event: dict[str, Any]) -> ValidationOutcome:
    """Validate a raw incoming dictionary against envelope and payload contracts.

    Returns a ValidationOutcome with is_valid=True and parsed envelope if valid,
    or is_valid=False with actionable diagnostic error messages if invalid.
    """
    errors: list[str] = []

    # 1. Validate envelope
    try:
        envelope = EventEnvelope.model_validate(raw_event)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(part) for part in err["loc"])
            errors.append(f"Envelope validation error at '{loc}': {err['msg']}")
        return ValidationOutcome(is_valid=False, error_messages=errors)

    # 2. Lookup registered payload schema
    schema_key = f"{envelope.event_type}:{envelope.schema_version}"
    payload_cls = SCHEMA_REGISTRY.get(schema_key)
    if not payload_cls:
        # Fallback to base model if minor version upgrade
        if envelope.event_type == "page_observation":
            payload_cls = SportsSeoObservationPayload
        else:
            return ValidationOutcome(
                is_valid=False,
                envelope=envelope,
                error_messages=[f"Unknown event type or schema version '{schema_key}'"],
            )

    # 3. Validate domain payload
    try:
        validated_obj = payload_cls.model_validate(envelope.payload)
        return ValidationOutcome(
            is_valid=True,
            envelope=envelope,
            validated_payload=validated_obj.model_dump(),
        )
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(part) for part in err["loc"])
            errors.append(f"Payload validation error at 'payload.{loc}': {err['msg']}")
        return ValidationOutcome(
            is_valid=False,
            envelope=envelope,
            error_messages=errors,
        )


def export_json_schema(schema_key: str = "page_observation:1.0.0") -> dict[str, Any]:
    """Export portable JSON Schema for cross-system validation."""
    model_cls = SCHEMA_REGISTRY.get(schema_key, SportsSeoObservationPayload)
    return model_cls.model_json_schema()
