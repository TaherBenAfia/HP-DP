"""Sharing manifests: the publication record of every Gold data product."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from src.platform.config import PlatformConfig, load_platform_config


def manifests_dir(config: PlatformConfig | None = None) -> Path:
    """Directory holding one JSON manifest per published product."""
    cfg = config or load_platform_config()
    return cfg.resolve(cfg.paths.sharing_manifest_dir)


def list_manifests(config: PlatformConfig | None = None) -> list[dict[str, Any]]:
    """Every manifest on disk, newest first."""
    root = manifests_dir(config)
    payloads: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.json")):
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        payload["manifest_path"] = str(path)
        payloads.append(payload)
    return sorted(payloads, key=lambda item: str(item.get("generated_at", "")), reverse=True)


def load_manifest(
    product: str,
    dataset_id: str | None = None,
    config: PlatformConfig | None = None,
) -> dict[str, Any]:
    """Load the manifest of one product (searching all datasets if needed)."""
    root = manifests_dir(config)
    candidates: list[Path]
    if dataset_id:
        candidates = [root / dataset_id / f"{product}.json"]
    else:
        candidates = sorted(root.glob(f"*/{product}.json"))
    for path in candidates:
        if path.exists():
            payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
            payload["manifest_path"] = str(path)
            return payload
    raise FileNotFoundError(f"no sharing manifest for product {product!r}")
