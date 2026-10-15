"""Read-only sharing API.

Serves the published Gold products over HTTP with JWT/OIDC protection.
The application only ever registers ``GET`` endpoints: the platform shares
data, it does not accept writes.

Run locally::

    uvicorn src.sharing.api:app --reload --port 8000
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.platform.config import load_platform_config
from src.platform.lineage import LineageStore
from src.platform.storage import Storage
from src.query.engine import DEFAULT_VIEWS
from src.sharing.manifests import list_manifests, load_manifest
from src.sharing.oidc import Principal, TokenValidationError, TokenValidator, token_from_header

READABLE_TABLES = sorted(set(DEFAULT_VIEWS) | {"observations"})
MAX_ROWS = 5000

_config = load_platform_config()
_validator = TokenValidator(_config)
_storage = Storage(_config)

app = FastAPI(
    title="Enterprise Analytics Platform - sharing API",
    version=_config.platform.version,
    description=(
        "Read-only access to published Gold data products. "
        "Every response is observational: statistics are associations, "
        "actions are transparent heuristics."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def _table_path(name: str) -> Path:
    if name in {"observations"}:
        return _storage.gold_domain_table("sports_seo", name)
    if name in READABLE_TABLES:
        return _storage.gold_table(name)
    raise HTTPException(status_code=404, detail=f"unknown table {name!r}")


def get_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """Authenticate the request and check the read scope."""
    try:
        token = token_from_header(authorization)
        principal = _validator.principal_for(token)
    except TokenValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not principal.has(_config.sharing.read_scope):
        raise HTTPException(
            status_code=403, detail=f"missing scope {_config.sharing.read_scope!r}"
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


@app.get("/health", summary="Liveness and configuration summary")
def health() -> dict[str, Any]:
    """Whether the API is up and how it authenticates callers."""
    return {
        "status": "ok",
        "platform": _config.platform.name,
        "version": _config.platform.version,
        "auth_required": _validator.enabled,
        "token_algorithm": _validator.algorithm,
        "read_only": _config.sharing.read_only,
        "tables": len(READABLE_TABLES),
    }


@app.get("/whoami", summary="Describe the calling principal")
def whoami(principal: PrincipalDep) -> dict[str, Any]:
    """The scopes and identity behind this request."""
    return principal.as_dict()


@app.get("/tables", summary="List published data products")
def tables(principal: PrincipalDep) -> dict[str, Any]:
    """Every readable product with its manifest metadata."""
    manifests = {Path(item.get("manifest_path", "")).stem: item for item in list_manifests(_config)}
    entries = []
    for name in READABLE_TABLES:
        path = _table_path(name)
        exists = _storage.exists(path)
        manifest = manifests.get(name, {})
        entries.append(
            {
                "name": name,
                "exists": exists,
                "rows": manifest.get("row_count"),
                "product_version": manifest.get("product_version"),
                "generated_at": manifest.get("generated_at"),
                "table": manifest.get("table"),
            }
        )
    return {"tables": entries, "principal": principal.subject}


@app.get("/tables/{name}", summary="Read rows of a published product")
def read_table(
    name: str,
    principal: PrincipalDep,
    limit: Annotated[int, Query(ge=1, le=MAX_ROWS)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """A window of rows plus schema and manifest metadata."""
    path = _table_path(name)
    if not _storage.exists(path):
        raise HTTPException(status_code=404, detail=f"table {name!r} is not published")
    frame = _storage.read(path)
    if frame is None:
        raise HTTPException(status_code=404, detail=f"table {name!r} is not published")
    window = frame.slice(offset, limit)
    try:
        manifest = load_manifest(name, config=_config)
    except FileNotFoundError:
        manifest = {}
    return {
        "name": name,
        "row_count": frame.height,
        "offset": offset,
        "limit": limit,
        "columns": frame.columns,
        "schema": {column: str(dtype) for column, dtype in frame.schema.items()},
        "rows": json.loads(window.write_json()),
        "manifest": manifest,
    }


@app.get("/lineage/{table_path:path}", summary="Lineage chain of a table")
def lineage(table_path: str, principal: PrincipalDep) -> dict[str, Any]:
    """Parents of a table, walked back to the original source file."""
    store = LineageStore(_config, _storage)
    chain = store.trace(table_path)
    if not chain:
        raise HTTPException(status_code=404, detail=f"no lineage recorded for {table_path!r}")
    return {"table": table_path, "chain": chain}


@app.get("/manifests", summary="Sharing manifests")
def manifests(principal: PrincipalDep) -> dict[str, Any]:
    """Publication records for every Gold product."""
    return {"manifests": list_manifests(_config)}


@app.get("/methods", summary="List analytical methods in registry")
def methods(principal: PrincipalDep) -> dict[str, Any]:
    """Inspect registered analytical and statistical methods."""
    from src.analytics.method_registry import create_default_method_registry
    registry = create_default_method_registry()
    return {"methods": [m.model_dump() for m in registry.list_methods()]}


@app.get("/quarantine", summary="Inspect quarantined records with diagnostics")
def quarantine(
    principal: PrincipalDep,
    tenant_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
) -> dict[str, Any]:
    """Retrieve quarantined records with full validation error diagnostics."""
    from src.platform.metadata_store import MetadataStore
    meta_path = _config.resolve("data/metadata.db")
    meta = MetadataStore(meta_path)
    records = meta.get_quarantine_records(tenant_id=tenant_id, limit=limit)
    return {"quarantine_records": records, "count": len(records)}


@app.get("/publications", summary="List published tenant products")
def publications(
    principal: PrincipalDep,
    requesting_tenant: Annotated[str, Query()] = "default",
) -> dict[str, Any]:
    """List accessible published data products for requesting tenant."""
    from src.platform.metadata_store import MetadataStore
    meta_path = _config.resolve("data/metadata.db")
    meta = MetadataStore(meta_path)
    with meta._get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM published_products
            WHERE is_revoked = 0
            ORDER BY published_at DESC LIMIT 50
            """
        ).fetchall()
        accessible = []
        for r in rows:
            allowed = json.loads(r["allowed_tenants"])
            if "*" in allowed or requesting_tenant.lower() in [a.lower() for a in allowed] or requesting_tenant.lower() == r["owner_tenant"].lower():
                accessible.append(
                    {
                        "publication_id": r["publication_id"],
                        "asset_id": r["asset_id"],
                        "owner_tenant": r["owner_tenant"],
                        "title": r["title"],
                        "published_at": r["published_at"],
                    }
                )
        return {"publications": accessible}


@app.exception_handler(TokenValidationError)
async def _token_error_handler(_request, exc: TokenValidationError) -> JSONResponse:
    """Turn token problems into a 401 with a readable reason."""
    return JSONResponse(status_code=401, content={"detail": str(exc)})
