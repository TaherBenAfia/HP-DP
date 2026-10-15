"""Provider example: Manage data product sharing.

Usage:
    python sharing/provider/provider.py publish DATASET_ID  # Publish data products
    python sharing/provider/provider.py grants              # List current grants
    python sharing/provider/provider.py grant CLIENT TABLE  # Grant access
    python sharing/provider/provider.py revoke CLIENT TABLE # Revoke access
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.platform.config import load_platform_config
from src.platform.storage import Storage
from src.sharing.manifests import list_manifests, manifests_dir


def get_grants_file() -> Path:
    """Get path to grants file."""
    return Path("sharing/policies/grants.json")


def load_grants() -> dict:
    """Load current grants."""
    grants_file = get_grants_file()
    if not grants_file.exists():
        return {"grants": []}
    return json.loads(grants_file.read_text(encoding="utf-8"))


def save_grants(grants: dict) -> None:
    """Save grants to file."""
    grants_file = get_grants_file()
    grants_file.parent.mkdir(parents=True, exist_ok=True)
    grants_file.write_text(json.dumps(grants, indent=2, sort_keys=True), encoding="utf-8")


def publish_dataset(dataset_id: str) -> None:
    """Publish data products for a dataset."""
    config = load_platform_config()
    manifests = list_manifests(config)

    dataset_manifests = [m for m in manifests if dataset_id in m.get("manifest_path", "")]

    if not dataset_manifests:
        print(f"No manifests found for dataset: {dataset_id}")
        return

    print(f"Published data products for {dataset_id}:")
    print("-" * 60)
    for manifest in dataset_manifests:
        print(f"  {manifest.get('product_id', 'N/A')}")
        print(f"    Table: {manifest.get('table', 'N/A')}")
        print(f"    Rows: {manifest.get('row_count', 'N/A')}")
        print(f"    Version: {manifest.get('product_version', 'N/A')}")
        print()


def list_grants() -> None:
    """List current grants."""
    grants = load_grants()

    if not grants["grants"]:
        print("No grants configured")
        return

    print("Current grants:")
    print("-" * 60)
    for grant in grants["grants"]:
        print(f"  Client: {grant['client_id']}")
        print(f"    Table: {grant['table']}")
        print(f"    Granted at: {grant['granted_at']}")
        print()


def grant_access(client_id: str, table: str) -> None:
    """Grant access to a client for a table."""
    from datetime import UTC, datetime

    grants = load_grants()

    # Check if grant already exists
    for grant in grants["grants"]:
        if grant["client_id"] == client_id and grant["table"] == table:
            print(f"Grant already exists: {client_id} -> {table}")
            return

    # Add new grant
    grants["grants"].append({
        "client_id": client_id,
        "table": table,
        "granted_at": datetime.now(UTC).isoformat(),
    })

    save_grants(grants)
    print(f"Granted: {client_id} -> {table}")


def revoke_access(client_id: str, table: str) -> None:
    """Revoke access from a client for a table."""
    grants = load_grants()

    # Find and remove grant
    original_count = len(grants["grants"])
    grants["grants"] = [
        g for g in grants["grants"]
        if not (g["client_id"] == client_id and g["table"] == table)
    ]

    if len(grants["grants"]) == original_count:
        print(f"Grant not found: {client_id} -> {table}")
        return

    save_grants(grants)
    print(f"Revoked: {client_id} -> {table}")


def main():
    parser = argparse.ArgumentParser(description="Provider example for data sharing")
    parser.add_argument("command", choices=["publish", "grants", "grant", "revoke"], help="Command to execute")
    parser.add_argument("args", nargs="*", help="Command arguments")
    args = parser.parse_args()

    if args.command == "publish":
        if len(args.args) < 1:
            print("Usage: provider.py publish <dataset_id>")
            return
        publish_dataset(args.args[0])
    elif args.command == "grants":
        list_grants()
    elif args.command == "grant":
        if len(args.args) < 2:
            print("Usage: provider.py grant <client_id> <table>")
            return
        grant_access(args.args[0], args.args[1])
    elif args.command == "revoke":
        if len(args.args) < 2:
            print("Usage: provider.py revoke <client_id> <table>")
            return
        revoke_access(args.args[0], args.args[1])


if __name__ == "__main__":
    main()
