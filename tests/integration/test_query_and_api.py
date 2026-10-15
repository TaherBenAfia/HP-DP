"""Query layer and sharing API integration tests."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


def test_query_engine_registers_gold_tables() -> None:
    from src.query import QueryEngine

    with QueryEngine() as engine:
        tables = engine.tables()
    for expected in ("action_recommendations", "statistical_evidence", "entity_metrics"):
        assert expected in tables


def test_query_returns_rows() -> None:
    from src.query import run_query

    frame = run_query(
        "SELECT primary_action, count(*) AS n FROM action_recommendations GROUP BY 1"
    )
    assert frame.height >= 5
    assert frame["n"].sum() == 1998


def test_query_limit_is_applied() -> None:
    from src.query import run_query

    frame = run_query("SELECT * FROM action_recommendations", limit=7)
    assert frame.height == 7


def test_query_unknown_view_fails() -> None:
    import duckdb

    from src.query import run_query

    with pytest.raises(duckdb.Error):
        run_query("SELECT * FROM does_not_exist")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from src.sharing.api import app

    return TestClient(app)


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["read_only"] is True


def test_tables_listing(client) -> None:
    payload = client.get("/tables").json()
    names = {entry["name"] for entry in payload["tables"]}
    assert "action_recommendations" in names


def test_table_read(client) -> None:
    payload = client.get("/tables/action_recommendations?limit=3").json()
    assert payload["row_count"] == 1998
    assert len(payload["rows"]) == 3
    assert "primary_action" in payload["columns"]


def test_unknown_table_is_404(client) -> None:
    assert client.get("/tables/not_a_table").status_code == 404


def test_lineage_endpoint(client) -> None:
    payload = client.get("/lineage/gold/action_recommendations").json()
    tables = [step["table"] for step in payload["chain"]]
    assert "silver/sports_seo" in tables


def test_manifests_endpoint(client) -> None:
    payload = client.get("/manifests").json()
    assert payload["manifests"]


def test_auth_enforced_when_configured() -> None:
    """require_auth flips the API into token-checking mode."""
    import jwt as pyjwt
    from fastapi.testclient import TestClient

    import src.sharing.api as api
    from src.platform.config import PlatformConfig
    from src.sharing.oidc import TokenValidator

    cfg = PlatformConfig()
    cfg.sharing.require_auth = True
    cfg.sharing.jwt_secret = "unit-test-secret-key-which-is-long-enough"
    original = api._validator
    api._validator = TokenValidator(cfg)
    try:
        client = TestClient(api.app)
        assert client.get("/tables").status_code == 401

        token = pyjwt.encode(
            {
                "sub": "tester",
                "scope": "analytics:read",
                "aud": cfg.sharing.audience,
                "iss": cfg.sharing.oidc_issuer,
                "exp": 4_102_444_800,  # 2100-01-01
            },
            cfg.sharing.jwt_secret,
            algorithm="HS256",
        )
        ok = client.get("/tables", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200

        wrong_scope = pyjwt.encode(
            {
                "sub": "tester",
                "scope": "openid",
                "aud": cfg.sharing.audience,
                "iss": cfg.sharing.oidc_issuer,
                "exp": 4_102_444_800,
            },
            cfg.sharing.jwt_secret,
            algorithm="HS256",
        )
        denied = client.get("/tables", headers={"Authorization": f"Bearer {wrong_scope}"})
        assert denied.status_code == 403
    finally:
        api._validator = original
