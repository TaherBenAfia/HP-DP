# Implementation Audit — StatSEO-fSport Repository

**Date:** 2026-10-10  
**Auditor:** Platform architect (automated)  
**Purpose:** Assess the existing implementation before evolving towards a general-purpose, asset-centric, multi-tenant analytics platform.

---

## 1. Repository Overview

| Property | Value |
|---|---|
| Python version | ≥3.11 (pyproject.toml); 3.13 available on host |
| Package manager | `uv` (referenced in README); `pip` as fallback |
| Build system | setuptools ≥68 |
| Primary framework | Polars + Delta Lake (via `deltalake` crate) |
| API | FastAPI + Uvicorn |
| Dashboard | Streamlit (7 pages, `app.py`) |
| Orchestration | Dagster + Temporal (`src/orchestration/`; in-process by default) |
| Auth | JWT/OIDC via `PyJWT` + Keycloak (docker-compose) |
| Test framework | pytest + pytest-asyncio |
| Linter/formatter | ruff |
| Type checker | mypy (strict mode) |

The project began as a research crawler (DVDCF) for sports SEO analysis and has evolved into a layered analytics platform with Bronze → Silver → Gold data pipeline, statistical evidence engine, action recommendations, and a sharing API.

---

## 2. Subsystem Audit

### 2.1 Configuration System

| Item | Detail |
|---|---|
| Files | `configs/platform.yaml`, `src/platform/config.py` |
| Status | **Fully implemented and working** |
| Tests | `tests/platform/test_config.py` |

**Assessment:** Well-designed typed Pydantic config with env-var expansion (`${VAR:-default}`). Covers paths, ingestion, silver, statistics, visibility, actions, data products, sharing, and logging. **Retain as-is.**

### 2.2 Datasource Contracts & Registry

| Item | Detail |
|---|---|
| Files | `src/ingestion/contracts.py`, `src/platform/datasource_registry.py`, `configs/datasources/registry.json`, `configs/datasources/sports_seo.json` |
| Status | **Fully implemented** |
| Tests | `tests/platform/test_datasource_registry.py` |

**Assessment:** Contracts define dataset schemas with field origins (RAW_SOURCE, DERIVED_FEATURE, etc.), validation rules, lineage metadata. The registry loads contracts, validates sources, computes fingerprints (SHA-256), and records ingestion metadata. One dataset registered: `sports_seo_tunisia_v1` with 213 source attributes.

**Retain and extend.** The contract model needs adaptation for event-based schemas alongside file-based datasets.

### 2.3 Ingestion Pipeline (Bronze)

| Item | Detail |
|---|---|
| Files | `src/ingestion/pipeline.py`, `src/ingestion/reader.py`, `src/ingestion/fingerprint.py`, `src/ingestion/validator.py` |
| Status | **Fully implemented for file-based CSV ingestion** |
| Tests | `tests/test_ingestion.py` |

**Assessment:** Reads CSV files, validates schema against contracts, stamps metadata columns (`_dataset_id`, `_run_id`, `_source_checksum`, etc.), writes to Delta Lake Bronze tables. Supports idempotent re-ingestion via source checksum comparison. Records ingestion manifests and lineage.

**Limitation:** Designed for batch file ingestion only. Does not support event streaming, Kafka/Redpanda consumers, or incremental event processing. Needs extension for the new streaming architecture.

### 2.4 Schema Validation

| Item | Detail |
|---|---|
| Files | `src/ingestion/validator.py` |
| Status | **Fully implemented** |

**Assessment:** Validates columns (required, extra, primary key), types (with castability detection for string→numeric, integer→boolean), and row counts. Reports warnings vs errors at appropriate severity levels.

**Retain.** This handles file-level validation well. Event-level validation will need a parallel Pydantic-based validator for streaming events.

### 2.5 Silver Transforms

| Item | Detail |
|---|---|
| Files | `src/transforms/sports_seo.py`, `src/transforms/common.py`, `src/transforms/quality.py` |
| Status | **Fully implemented for sports SEO domain** |
| Tests | Exercised by integration tests |

**Assessment:** Normalizes URLs, domains, strings, nulls; casts types to contract; deduplicates by primary key; applies row-level quality rules; quarantines invalid rows (34 quarantined from 2032 input rows). Records lineage. Version-stamped (`SILVER_VERSION = "1.0.0"`).

**Domain-specific.** The `sports_seo.py` transform is tightly coupled to the sports SEO schema. `common.py` and `quality.py` provide reusable functions. **Retain common transforms; domain-specific transforms remain in their domain module.**

### 2.6 Feature Registry

| Item | Detail |
|---|---|
| Files | `src/analytics/feature_registry.py`, `src/domains/sports_seo/features.py` |
| Status | **Fully implemented** |
| Tests | Exercised through statistics and action engine tests |

**Assessment:** Typed `FeatureSpec` with name, family, kind (NUMERIC/BINARY/CATEGORICAL/TEXT), role (IDENTITY/OUTCOME/FEATURE/COMPETITOR/DECISION), directionality, and availability. Sports SEO domain registers ~174 features across 6 action families. Used by statistical engine, action engine, and metrics.

**Retain.** This is a good foundation for the planned analytical method registry.

### 2.7 Visibility Engine (SVTP)

| Item | Detail |
|---|---|
| Files | `src/analytics/visibility.py` |
| Status | **Fully implemented** |
| Tests | `tests/unit/test_visibility.py` |

**Assessment:** Computes Search Visibility Trend Proxy from rank, entity, and demand signals. Explicitly marks demand component as unavailable. Produces transparent component scores.

**Retain.** Domain-specific but well-structured with clear separation of concerns.

### 2.8 Statistical Evidence Engine

| Item | Detail |
|---|---|
| Files | `src/statistics/engine.py` |
| Status | **Fully implemented** |
| Tests | `tests/unit/test_statistics_engine.py` (Spearman, Mann-Whitney, chi-square, BH FDR) |

**Assessment:** Implements Spearman rank correlation, Mann-Whitney U, chi-square independence tests with Benjamini-Hochberg FDR correction. Reports effect sizes, direction, sample sizes. Skips zero-variance and small-sample features. Enforces "associational only" interpretation.

**Retain.** This is a genuine, well-tested statistical engine. It can serve as the first registered analytical method in the new method registry.

### 2.9 Action Engine

| Item | Detail |
|---|---|
| Files | `src/analytics/action_engine.py`, `src/domains/sports_seo/actions.py` |
| Status | **Fully implemented** |
| Tests | `tests/unit/test_action_engine.py` |

**Assessment:** Scores 6 action families based on feature deficits, re-weights by FDR-significant evidence, assigns priorities (high/medium/low), generates transparent recommendations with JSON evidence.

**Retain.** Domain-specific logic in `actions.py`; generic engine in `action_engine.py`.

### 2.10 Gold Pipeline (Domain Publishing)

| Item | Detail |
|---|---|
| Files | `src/domains/sports_seo/pipeline.py` |
| Status | **Fully implemented** |
| Tests | `tests/integration/test_pipeline.py` |

**Assessment:** Orchestrates Silver → features → statistics → actions → metrics → quality → publish. Produces 12 Delta tables. Records lineage with parent table relationships. Stamps data product versions. Writes sharing manifests.

**Limitation:** Hard-coded sequential pipeline. No dependency graph, no selective recomputation, no caching. **This is the primary target for the DAG-based refactoring.**

### 2.11 Domain Adapter System

| Item | Detail |
|---|---|
| Files | `src/domains/base.py`, `src/domains/sports_seo/domain.py` |
| Status | **Fully implemented** |

**Assessment:** Abstract `DomainAdapter` base class with `key`, `features`, `build_features`, `build_metrics`, `run_statistics`, `build_actions`. Sports SEO is the only concrete implementation. `DomainContext` provides access to config, registry, and dataset.

**Retain.** Good plugin architecture. The new platform can treat each domain as a set of registered tasks/methods.

### 2.12 Analytical Base Model

| Item | Detail |
|---|---|
| Files | `src/analytics/base_model.py` |
| Status | **Fully implemented** |

**Assessment:** Generic analytical model mapping (entity, document, query, outcome, position, decision, score, priority). Provides observations, entities, documents, queries views. Used by metrics and exports.

**Retain.**

### 2.13 Query Engine

| Item | Detail |
|---|---|
| Files | `src/query/engine.py` |
| Status | **Fully implemented** |
| Tests | `tests/integration/test_query_and_api.py` |

**Assessment:** DuckDB-backed query engine that registers Delta tables as views and exposes SQL access. Bounded row limits.

**Retain.**

### 2.14 Sharing API

| Item | Detail |
|---|---|
| Files | `src/sharing/api.py`, `src/sharing/oidc.py`, `src/sharing/manifests.py` |
| Status | **Fully implemented** |
| Tests | `tests/integration/test_query_and_api.py`, `tests/integration/test_data_sharing.py` |

**Assessment:** FastAPI read-only API with JWT/OIDC authentication. Endpoints: `/health`, `/whoami`, `/tables`, `/tables/{name}`, `/lineage/{table}`, `/manifests`. CORS configured. Two auth modes: Keycloak RS256 and local HS256.

**Retain.** Will need extension for tenant-scoped access and the new asset inspection endpoints.

### 2.15 Data Lineage

| Item | Detail |
|---|---|
| Files | `src/platform/lineage.py` |
| Status | **Fully implemented** |

**Assessment:** Append-only lineage store backed by Delta table. Records table, layer, run_id, parent tables, source checksums, transformation/model versions. Supports `trace()` for walking the lineage chain. Idempotent per `(table, run_id)`.

**Retain.** The concept aligns with the planned materialization records. Needs extension to support asset-level lineage with input fingerprints.

### 2.16 Storage Layer

| Item | Detail |
|---|---|
| Files | `src/platform/storage.py` |
| Status | **Fully implemented** |

**Assessment:** Local filesystem Delta Lake I/O via `deltalake` (Rust-backed). Resolves Bronze/Silver/Gold table paths. Handles null-dtype columns. Provides read, write, append, history operations.

**Retain.** Solid foundation. A storage interface can wrap this for future MinIO/S3 support.

### 2.17 CLI

| Item | Detail |
|---|---|
| Files | `platform_cli/main.py`, `platform_cli/__main__.py` |
| Status | **Fully implemented** |

**Assessment:** Commands: domain, datasource, ingest, silver, analytics, stats, query, lineage, share, run. All produce structured JSON output.

**Retain and extend** for new asset-graph commands.

### 2.18 Dashboard

| Item | Detail |
|---|---|
| Files | `app.py`, `app_pages/` |
| Status | **Implemented** (not audited in detail) |

**Assessment:** 7-page Streamlit dashboard. Low priority for the first platform milestone.

### 2.19 Legacy DVDCF Crawler

| Item | Detail |
|---|---|
| Files | `src/pipeline.py`, `src/modules/`, `src/config_loader.py`, `src/interfaces/` |
| Status | **Legacy, not part of the analytics platform** |
| Tests | `tests/test_pipeline_crawl.py`, `tests/test_deduplicator.py`, etc. |

**Assessment:** The original web crawler framework. `src/pipeline.py` (914 lines) orchestrates URL discovery → HTTP fetching → DOM extraction → NLP → feature engineering → graph storage. `src/modules/` contains crawler, discovery, extraction, features, graph, NLP, scheduler, and storage submodules. `src/config_loader.py` and `src/interfaces/records.py` define crawler-specific types.

**Preserve but do not modify.** These are frozen research artifacts. The new platform does not depend on them.

### 2.20 Orchestration

| Item | Detail |
|---|---|
| Files | `src/orchestration/dagster_defs.py`, `src/orchestration/temporal_defs.py` |
| Status | **Implemented** |

**Assessment:** Dagster assets mirror the Bronze→Silver→Gold→Iceberg→OpenLineage
flow; Temporal activities wrap the same stage functions and run in-process when
no `TEMPORAL_ADDRESS` is configured. The legacy Prefect `flows/` were removed.

### 2.21 Docker Compose

| Item | Detail |
|---|---|
| Files | `docker-compose.yml`, `Dockerfile`, `.devcontainer/` |
| Status | **Implemented** |

**Assessment:** Three services: Keycloak (auth), API (FastAPI), Dashboard (Streamlit),
plus an optional Redpanda streaming profile. Orchestration runs in-process (Dagster
`dagster dev` / Temporal `temporal server start-dev`). MinIO is not included.

### 2.22 Data Files

The `data/` directory contains:
- **Research datasets:** `dvdcf.db` (37 MB), `fast_scraped.db` (720 MB), various CSV files
- **Raw source:** `data/raw/sports_seo/sports_seo_page_features.csv` (the canonical input)
- **Generated layers:** `data/bronze/`, `data/silver/`, `data/gold/` (gitignored, rebuilt by pipeline)
- **DuckDB:** `data/analytics.duckdb` (query engine database)

**Do not delete** research databases or source datasets. Generated layers are rebuildable.

---

## 3. Duplicate and Overlapping Responsibilities

| Concern | Locations | Resolution |
|---|---|---|
| Configuration | `src/config_loader.py` (DVDCF) vs `src/platform/config.py` (platform) | Keep both. They serve different systems. |
| Pipeline orchestration | `src/pipeline.py` (crawler) vs `src/domains/sports_seo/pipeline.py` (Gold) vs `src/orchestration/` (Dagster/Temporal) | The Gold pipeline is canonical. Dagster/Temporal definitions are thin wrappers. Crawler pipeline is legacy. |
| Data models | `src/interfaces/records.py` (crawler types) vs `src/ingestion/contracts.py` (platform contracts) | Separate concerns. No conflict. |
| Storage | `src/modules/storage/` (crawler) vs `src/platform/storage.py` (platform) | Separate. Platform storage is canonical. |

**No harmful duplication found.** The legacy crawler and the analytics platform coexist without conflict because they share no code paths.

---

## 4. What Does NOT Exist Yet

These components are **required by the specification** but have **no existing implementation**:

| Component | Status |
|---|---|
| Event broker (Redpanda/Kafka) | Not present |
| Synthetic event generator | Not present |
| Event-based ingestion consumer | Not present (file-based only) |
| Event schema validation (Pydantic/JSON Schema) | Not present for events (exists for file contracts) |
| Asset registry (DAG of versioned assets) | Not present |
| Task registry | Not present |
| Dependency graph with cycle detection | Not present |
| Execution planner | Not present |
| DAG-aware executor | Not present |
| Computation cache | Not present |
| Materialization records | Partial (lineage exists, but no cache keys or input fingerprints) |
| Analytical method registry | Not present (feature registry exists, but not method-level) |
| Tenant isolation | Not present (no tenant_id concept in data model) |
| SQLite metadata store | Not present (metadata stored in Delta tables) |
| Incremental recomputation | Not present (full recompute every run) |
| MinIO object storage | Not present |

---

## 5. Test Baseline

**Test suite:** 182 test cases executed across:
- `tests/unit/` — Statistics engine (9), action engine (9), visibility (7) = 25 tests
- `tests/platform/` — Config (5), datasource registry (7) = 12 tests
- `tests/integration/` — Full pipeline (8), query engine & API (11), data sharing (14) = 33 tests
- `tests/` (root) — Ingestion (15), NLP (19), deduplicator (24), graph storage (15), PaA processor (9), crawler pipeline (7), dry run (2), scheduler (12), feature engineer (9) = 112 tests

**Baseline Run Results (2026-10-10):**
- Command: `pytest --tb=short`
- Python runtime: `Python 3.13.14 (win32)`
- Result: **179 passed, 3 xpassed, 0 failed** in 12.60s
- Pre-existing failures: 0
- Baseline health: **100% passing**

---

## 6. Proposed Change Plan

### Phase 1 (Milestone A → B): Foundation

1. **Keep all existing code unchanged** — no deletions, no refactoring of working modules.
2. **Add SQLite metadata store** (`src/platform/metadata_store.py`) for asset, task, and materialization registries.
3. **Add event schema** (`src/ingestion/event_schema.py`) — Pydantic models and JSON Schema for streaming events.
4. **Add synthetic event generator** (`src/generators/synthetic.py`) — deterministic, seed-based, multi-tenant.
5. **Add Redpanda to docker-compose** and implement producer/consumer services.
6. **Add event ingestion consumer** that validates, quarantines, deduplicates, and persists to Bronze.
7. **Add contract evolution test** showing backward-compatible schema change.

### Phase 2 (Milestone C → D): Asset Graph & Caching

8. **Add asset registry** with dependency graph, cycle detection, topological ordering.
9. **Add task registry** and execution planner.
10. **Add DAG-aware executor** with bounded concurrency.
11. **Add computation cache** with deterministic cache keys.
12. **Add materialization records** with input fingerprints.

### Phase 3 (Milestone E → F): Methods & Tenants

13. **Register statistical engine as Method A** (descriptive aggregation).
14. **Register comparison method as Method B** (statistical comparison).
15. **Add tenant_id to asset model** and enforce isolation.
16. **Add authorization layer** for asset access.

### Phase 4 (Milestone G): Polish

17. **Extend CLI** with asset-graph commands.
18. **Add inspection API endpoints**.
19. **Run incremental efficiency experiment**.
20. **Complete documentation**.

### Guiding principles:
- New code goes in `src/platform/` (generic) or `src/generators/` (synthetic data).
- Existing `src/ingestion/`, `src/transforms/`, `src/analytics/`, `src/statistics/`, `src/domains/` remain untouched unless integration requires changes.
- Tests for new code go in `tests/unit/` and `tests/integration/`.
- All new components have explicit interfaces and typed models.

---

## 7. Reuse Summary

| Existing Component | Reuse in New Platform |
|---|---|
| `src/platform/config.py` | Direct reuse; extend with new sections |
| `src/platform/storage.py` | Direct reuse; add storage interface |
| `src/platform/lineage.py` | Evolve into materialization records |
| `src/ingestion/contracts.py` | Direct reuse for file sources |
| `src/ingestion/validator.py` | Direct reuse for file validation |
| `src/analytics/feature_registry.py` | Foundation for method registry |
| `src/statistics/engine.py` | Register as analytical method |
| `src/analytics/action_engine.py` | Register as analytical method |
| `src/sharing/api.py` | Extend with new endpoints |
| `src/sharing/oidc.py` | Direct reuse; extend for tenant tokens |
| `src/query/engine.py` | Direct reuse |
| `platform_cli/main.py` | Extend with new commands |
| `src/domains/base.py` | Adapt domain plugins to register tasks |

---

*Baseline established and verified: 179 passed, 3 xpassed, 0 failures.*
