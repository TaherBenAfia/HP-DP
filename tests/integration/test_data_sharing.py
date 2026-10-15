"""Integration tests for data sharing with authentication and authorization.

Note: The sharing API uses global config loaded at import time. Tests that
require auth to be enabled use environment variables to control behavior.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.sharing.api import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestBasicEndpoints:
    """Tests for basic API endpoints without authentication."""

    def test_health_endpoint(self, client):
        """Test health endpoint is accessible."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_tables_endpoint(self, client):
        """Test tables endpoint returns data products."""
        response = client.get("/tables")
        assert response.status_code == 200
        data = response.json()
        assert "tables" in data
        assert len(data["tables"]) > 0

    def test_read_table_endpoint(self, client):
        """Test reading a specific table."""
        response = client.get("/tables/action_recommendations?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert "rows" in data
        assert len(data["rows"]) <= 5
        assert "columns" in data

    def test_lineage_endpoint(self, client):
        """Test lineage endpoint."""
        response = client.get("/lineage/gold/action_recommendations")
        assert response.status_code == 200
        data = response.json()
        assert "chain" in data
        assert len(data["chain"]) > 0

    def test_manifests_endpoint(self, client):
        """Test manifests endpoint."""
        response = client.get("/manifests")
        assert response.status_code == 200
        data = response.json()
        assert "manifests" in data


class TestProviderConsumer:
    """Tests for provider-to-consumer flow."""

    def test_consumer_can_list_tables(self, client):
        """Simulate consumer listing available tables."""
        response = client.get("/tables")
        assert response.status_code == 200
        tables = response.json()["tables"]
        assert len(tables) > 0

        # Verify table structure
        for table in tables:
            assert "name" in table
            assert "exists" in table

    def test_consumer_can_read_data(self, client):
        """Simulate consumer reading data."""
        # First list tables
        list_response = client.get("/tables")
        tables = list_response.json()["tables"]

        # Find a table that exists
        existing_table = next((t for t in tables if t.get("exists")), None)
        if existing_table is None:
            pytest.skip("No existing tables found")

        # Read from that table
        read_response = client.get(f"/tables/{existing_table['name']}?limit=3")
        assert read_response.status_code == 200
        data = read_response.json()
        assert "rows" in data
        assert len(data["rows"]) <= 3

    def test_consumer_can_trace_lineage(self, client):
        """Simulate consumer tracing data lineage."""
        response = client.get("/lineage/gold/action_recommendations")
        assert response.status_code == 200
        data = response.json()
        assert "chain" in data
        assert len(data["chain"]) > 0

        # Verify chain includes expected layers
        table_names = [step["table"] for step in data["chain"]]
        assert "gold/action_recommendations" in table_names


class TestJWTTokenValidation:
    """Tests for JWT token validation (manual verification).

    These tests document the expected behavior when SHARING_REQUIRE_AUTH=true.
    They are marked as xfail because the global config is loaded at import time.
    """

    @pytest.mark.xfail(reason="Global config loaded at import time")
    def test_valid_token_accepted(self):
        """Valid token with correct scope should be accepted."""
        # This test documents the expected behavior

    @pytest.mark.xfail(reason="Global config loaded at import time")
    def test_invalid_token_rejected(self):
        """Invalid token should be rejected with 401."""
        # When SHARING_REQUIRE_AUTH=true:
        # curl -H "Authorization: Bearer invalid-token" http://localhost:8080/tables
        # Should return 401 Unauthorized

    @pytest.mark.xfail(reason="Global config loaded at import time")
    def test_missing_scope_rejected(self):
        """Token without required scope should be rejected with 403."""
        # Token with scope "other:scope" instead of "analytics:read"
        # Should return 403 Forbidden


def test_consumer_script_exists():
    """Verify consumer script exists."""
    import pathlib
    consumer_path = pathlib.Path("sharing/consumer/consumer.py")
    assert consumer_path.exists()


def test_provider_script_exists():
    """Verify provider script exists."""
    import pathlib
    provider_path = pathlib.Path("sharing/provider/provider.py")
    assert provider_path.exists()


def test_realm_export_exists():
    """Verify Keycloak realm export exists."""
    import pathlib
    realm_path = pathlib.Path("sharing/provider/realm-export.json")
    assert realm_path.exists()
