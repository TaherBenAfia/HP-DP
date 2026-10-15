"""Tenant isolation, access authorization, and controlled data product publication.

Enforces tenant boundaries at read, execution, and cache boundaries.
Supports versioned read-only publication to specified tenants and instant revocation.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from src.platform.metadata_store import MetadataStore


class PublishedProduct(BaseModel):
    """A published, versioned analytical product available for authorized sharing."""

    publication_id: str
    asset_id: str
    owner_tenant: str
    materialization_id: str
    title: str
    allowed_tenants: list[str] = Field(default_factory=lambda: ["*"])
    published_at: str
    is_revoked: bool = False
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class PublicationManager:
    """Manages multi-tenant access control and publication lifecycle."""

    def __init__(self, metadata_store: MetadataStore) -> None:
        self.metadata_store = metadata_store
        self._init_table()

    def _init_table(self) -> None:
        with self.metadata_store._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS published_products (
                    publication_id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL,
                    owner_tenant TEXT NOT NULL,
                    materialization_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    allowed_tenants TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    is_revoked INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_pub_owner ON published_products(owner_tenant);
                CREATE INDEX IF NOT EXISTS idx_pub_asset ON published_products(asset_id);
                """
            )

    def publish_asset(
        self,
        asset_id: str,
        owner_tenant: str,
        materialization_id: str,
        title: str,
        allowed_tenants: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PublishedProduct:
        """Publish a materialized asset with controlled tenant authorization."""
        pub_id = f"pub_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        allowed = allowed_tenants or ["*"]

        product = PublishedProduct(
            publication_id=pub_id,
            asset_id=asset_id,
            owner_tenant=owner_tenant,
            materialization_id=materialization_id,
            title=title,
            allowed_tenants=allowed,
            published_at=now,
            is_revoked=False,
            metadata_json=metadata or {},
        )

        with self.metadata_store._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO published_products (
                    publication_id, asset_id, owner_tenant, materialization_id,
                    title, allowed_tenants, published_at, is_revoked, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product.publication_id,
                    product.asset_id,
                    product.owner_tenant,
                    product.materialization_id,
                    product.title,
                    json.dumps(product.allowed_tenants),
                    product.published_at,
                    0,
                    json.dumps(product.metadata_json),
                ),
            )
        return product

    def revoke_publication(self, publication_id: str, owner_tenant: str) -> bool:
        """Revoke a published product. Only the owner tenant can revoke it."""
        with self.metadata_store._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE published_products
                SET is_revoked = 1
                WHERE publication_id = ? AND owner_tenant = ?
                """,
                (publication_id, owner_tenant),
            )
            return cur.rowcount > 0

    def check_access(
        self,
        requesting_tenant: str,
        target_asset_owner: str,
        publication_id: str | None = None,
    ) -> tuple[bool, str]:
        """Verify whether requesting_tenant is authorized to access the asset.

        Rules:
        1. Owner tenant always has full access to their private assets.
        2. Non-owner cannot access another tenant's private assets.
        3. If a publication_id is provided:
           - Must not be revoked.
           - Requesting tenant must be in allowed_tenants (or allowed_tenants contains '*').
        """
        # Rule 1: Direct ownership
        if requesting_tenant.lower() == target_asset_owner.lower():
            return True, "Authorized (owner)"

        # Rule 2: Without publication, access is forbidden
        if not publication_id:
            return False, f"Access denied: private asset owned by '{target_asset_owner}'"

        # Rule 3: Check publication status
        with self.metadata_store._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM published_products WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
            if not row:
                return False, f"Access denied: publication '{publication_id}' not found"

            if bool(row["is_revoked"]):
                return False, "Access denied: publication has been revoked"

            allowed: list[str] = json.loads(row["allowed_tenants"])
            req_clean = requesting_tenant.lower()
            if "*" in allowed or req_clean in [a.lower() for a in allowed]:
                return True, "Authorized (published read-only)"

            return False, f"Access denied: tenant '{requesting_tenant}' not in authorized recipient list"
