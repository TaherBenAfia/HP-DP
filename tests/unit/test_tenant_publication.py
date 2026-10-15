"""Unit tests for tenant access control, publication, and revocation."""

from pathlib import Path
from src.platform.metadata_store import MetadataStore
from src.platform.publication import PublicationManager


def test_tenant_access_and_revocation(tmp_path: Path) -> None:
    """Validate tenant boundaries: private isolation, selective publication, and instant revocation."""
    meta = MetadataStore(tmp_path / "meta_tenant.db")
    mgr = PublicationManager(meta)

    # 1. Private access: owner can access, non-owner denied
    ok, _ = mgr.check_access(requesting_tenant="tenant_alpha", target_asset_owner="tenant_alpha")
    assert ok is True

    denied, msg = mgr.check_access(requesting_tenant="tenant_beta", target_asset_owner="tenant_alpha")
    assert denied is False
    assert "private asset" in msg

    # 2. Publish asset specifically to tenant_beta
    pub = mgr.publish_asset(
        asset_id="gold_rankings",
        owner_tenant="tenant_alpha",
        materialization_id="mat_12345",
        title="Q3 League Rankings",
        allowed_tenants=["tenant_beta"],
    )

    # 3. Now tenant_beta has access
    ok_pub, _ = mgr.check_access(
        requesting_tenant="tenant_beta",
        target_asset_owner="tenant_alpha",
        publication_id=pub.publication_id,
    )
    assert ok_pub is True

    # But third-party tenant_gamma is still denied
    denied_gamma, msg_gamma = mgr.check_access(
        requesting_tenant="tenant_gamma",
        target_asset_owner="tenant_alpha",
        publication_id=pub.publication_id,
    )
    assert denied_gamma is False
    assert "not in authorized recipient list" in msg_gamma

    # 4. Revocation by owner
    revoked = mgr.revoke_publication(publication_id=pub.publication_id, owner_tenant="tenant_alpha")
    assert revoked is True

    # 5. Access is now immediately denied to tenant_beta
    denied_after_revocation, msg_rev = mgr.check_access(
        requesting_tenant="tenant_beta",
        target_asset_owner="tenant_alpha",
        publication_id=pub.publication_id,
    )
    assert denied_after_revocation is False
    assert "revoked" in msg_rev

    # 6. Non-owner cannot revoke owner's publication
    fraud_rev = mgr.revoke_publication(publication_id=pub.publication_id, owner_tenant="tenant_beta")
    assert fraud_rev is False
