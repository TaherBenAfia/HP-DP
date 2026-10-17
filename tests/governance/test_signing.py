"""Tests for Ed25519 artifact signing."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from src.governance.signing import (
    canonical_json,
    load_or_create_keypair,
    sign_artifact_file,
    sign_payload,
    verify_artifact_file,
    verify_payload,
)
from src.platform.config import GovernanceConfig, PlatformConfig


def _config(tmp_path: Path) -> PlatformConfig:
    return PlatformConfig(
        repo_root=tmp_path,
        governance=GovernanceConfig(signing_key_path="keys/artifact_signing.key"),
    )


def test_keypair_create_and_reload_identical(tmp_path: Path) -> None:
    key_path = tmp_path / "keys" / "k.key"
    private_one, public_one = load_or_create_keypair(key_path)
    assert key_path.exists()

    private_two, public_two = load_or_create_keypair(key_path)
    assert private_one == private_two
    assert public_one == public_two


def test_sign_verify_roundtrip(tmp_path: Path) -> None:
    private_pem, public_pem = load_or_create_keypair(tmp_path / "k.key")
    payload = {"b": 2, "a": 1}
    record = sign_payload(payload, private_pem)
    assert record["algorithm"] == "ed25519"
    assert verify_payload(payload, record, public_pem) is True


def test_tampered_payload_fails(tmp_path: Path) -> None:
    private_pem, public_pem = load_or_create_keypair(tmp_path / "k.key")
    record = sign_payload({"a": 1, "b": 2}, private_pem)
    assert verify_payload({"a": 1, "b": 3}, record, public_pem) is False


def test_tampered_signature_fails(tmp_path: Path) -> None:
    private_pem, public_pem = load_or_create_keypair(tmp_path / "k.key")
    payload = {"a": 1}
    record = sign_payload(payload, private_pem)
    tampered = dict(record)
    tampered["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
    assert verify_payload(payload, tampered, public_pem) is False


def test_verify_payload_handles_bad_input(tmp_path: Path) -> None:
    private_pem, public_pem = load_or_create_keypair(tmp_path / "k.key")
    record = sign_payload({"a": 1}, private_pem)
    assert verify_payload({"a": 1}, {}, public_pem) is False
    assert verify_payload({"a": 1}, {"signature": "not-base64"}, None) is False
    assert verify_payload({"a": 1}, record, public_pem) is True


def test_canonical_json_is_stable() -> None:
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})
    assert canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_artifact_file_sign_and_verify(tmp_path: Path) -> None:
    config = _config(tmp_path)
    artifact = (
        tmp_path / "sharing" / "manifests" / "ds" / "action_recommendations.json"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({"x": 1}, indent=2), encoding="utf-8")

    signed = sign_artifact_file(config, artifact)
    assert signed.name == "action_recommendations.signed.json"
    assert signed.exists()
    assert verify_artifact_file(config, signed) is True

    artifact.write_text(json.dumps({"x": 2}, indent=2), encoding="utf-8")
    assert verify_artifact_file(config, signed) is False
