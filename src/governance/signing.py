"""Ed25519 artifact signing helpers.

Provides canonical JSON hashing, keypair loading/creation, payload signing and
verification, plus file-level convenience wrappers used by the governance and
recommendation modules.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.platform.config import PlatformConfig


def canonical_json(payload: Mapping[str, Any]) -> bytes:
    """Serialise ``payload`` to deterministic, key-sorted UTF-8 JSON bytes."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _restrict_perms(path: Path) -> None:
    """Best-effort restrictive permissions on the private key file."""
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def load_or_create_keypair(key_path: Path) -> tuple[bytes, bytes]:
    """Load an Ed25519 keypair from ``key_path`` or create one in place.

    Returns ``(private_pem, public_pem)``. Parent directories are created when
    the key is absent and a new private key file is written with restrictive
    permissions (best-effort on Windows).
    """
    key_path = Path(key_path)
    if key_path.exists():
        private_pem = key_path.read_bytes()
        private_key = serialization.load_pem_private_key(private_pem, password=None)
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return private_pem, public_pem

    key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_path.write_bytes(private_pem)
    _restrict_perms(key_path)
    return private_pem, public_pem


def sign_payload(payload: Mapping[str, Any], private_key_pem: bytes) -> dict[str, str]:
    """Sign ``payload`` and return an algorithm/hash/signature/public-key record."""
    data = canonical_json(payload)
    payload_sha256 = hashlib.sha256(data).hexdigest()
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(data)
    raw_public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return {
        "algorithm": "ed25519",
        "payload_sha256": payload_sha256,
        "signature": base64.b64encode(signature).decode("ascii"),
        "public_key": base64.b64encode(raw_public).decode("ascii"),
    }


def _load_public_key(data: bytes | str | None) -> Any:
    """Interpret PEM, raw, or base64-encoded public key material."""
    if data is None:
        raise ValueError("no public key provided")
    if isinstance(data, str):
        data = data.encode("utf-8")
    if b"BEGIN" in data:
        return serialization.load_pem_public_key(data)
    raw = base64.b64decode(data, validate=True)
    if len(raw) == 32:
        return ed25519.Ed25519PublicKey.from_public_bytes(raw)
    if b"BEGIN" in raw:
        return serialization.load_pem_public_key(raw)
    raise ValueError("unrecognized public key material")


def verify_payload(
    payload: Mapping[str, Any],
    record: Mapping[str, Any],
    public_key_pem: bytes | str | None = None,
) -> bool:
    """Verify ``record`` against ``payload``; return ``False`` on any mismatch."""
    try:
        if not isinstance(record, Mapping):
            return False
        expected_sha = str(record.get("payload_sha256", ""))
        signature_b64 = str(record.get("signature", ""))
        if not expected_sha or not signature_b64:
            return False

        data = canonical_json(payload)
        if hashlib.sha256(data).hexdigest() != expected_sha:
            return False

        key_source = public_key_pem if public_key_pem else record.get("public_key")
        public_key = _load_public_key(key_source)
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, data)
        return True
    except Exception:
        return False


def sign_artifact_file(config: PlatformConfig, artifact_path: Path) -> Path:
    """Sign the JSON artifact at ``artifact_path`` into a sibling ``*.signed.json``."""
    artifact_path = Path(artifact_path)
    key_path = config.resolve(config.governance.signing_key_path)
    private_pem, _public_pem = load_or_create_keypair(key_path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    record = sign_payload(payload, private_pem)
    signed_path = artifact_path.with_name(artifact_path.stem + ".signed.json")
    signed_path.write_text(
        json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
    )
    return signed_path


def verify_artifact_file(config: PlatformConfig, signed_path: Path) -> bool:
    """Verify a ``*.signed.json`` file against its original artifact sibling."""
    signed_path = Path(signed_path)
    name = signed_path.name
    if name.endswith(".signed.json"):
        original = signed_path.with_name(name[: -len(".signed.json")] + ".json")
    else:
        original = signed_path
    if not signed_path.exists() or not original.exists():
        return False
    try:
        payload = json.loads(original.read_text(encoding="utf-8"))
        record = json.loads(signed_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    try:
        key_path = config.resolve(config.governance.signing_key_path)
        _private_pem, public_pem = load_or_create_keypair(key_path)
    except Exception:
        public_pem = None
    return verify_payload(payload, record, public_pem)
