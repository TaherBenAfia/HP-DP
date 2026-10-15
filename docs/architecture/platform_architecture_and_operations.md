# Platform Architecture & Operational Guide

## 1. Executive Summary & Objective

This platform evolves StatSEO-fSport from a domain-specific research crawler into a local-first, multi-tenant, asset-centric analytical platform. It preserves the sports SEO use-case while decoupling ingestion, contracts, dependency planning, analytical method execution, caching, and access authorization.

---

## 2. Component Responsibilities & Module Map

| Component | Module | Responsibility |
|---|---|---|
| **Operational Metadata Store** | [`src/platform/metadata_store.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/platform/metadata_store.py) | Embedded SQLite in WAL mode. Manages ingestion checkpoints, event deduplication indexes, quarantine logs with validation diagnostics, and materialization records. |
| **Event Contracts & Validation** | [`src/ingestion/event_schema.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/ingestion/event_schema.py) | Versioned `EventEnvelope` and domain payload models (`SportsSeoObservationPayload`). Diagnostic error extraction and JSON Schema export. |
| **Synthetic Event Generator** | [`src/generators/synthetic.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/generators/synthetic.py) | Seed-based deterministic event streaming supporting multi-tenant partitioning, duplicate injection, and malformed anomaly injection. |
| **Event Broker Abstraction** | [`src/ingestion/broker.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/ingestion/broker.py) | Thread-safe `InMemoryBroker` for zero-dependency local testing alongside Redpanda Kafka compatibility in Docker Compose. |
| **Event Ingestion Consumer** | [`src/ingestion/event_consumer.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/ingestion/event_consumer.py) | Consumes streams, routes invalid records to quarantine with diagnostics, enforces idempotent event deduplication, writes accepted rows to Bronze Delta tables with provenance, and commits durable checkpoints. |
| **Asset Dependency Graph** | [`src/platform/asset_graph.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/platform/asset_graph.py) | Directed Acyclic Graph (DAG) of versioned `DataAsset` and `TaskDefinition` nodes with cycle detection and topological sorting. |
| **Execution Planner & Cache** | [`src/platform/planner.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/platform/planner.py) | Resolves requested target assets, computes deterministic cache keys across tenant scope, input fingerprints, and task versions, and triggers selective recomputation. |
| **DAG-Aware Executor** | [`src/platform/executor.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/platform/executor.py) | Runs planned tasks in topological dependency order, skips valid cached tasks, records durable materialization records, and tracks execution metrics. |
| **Analytical Method Registry** | [`src/analytics/method_registry.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/analytics/method_registry.py) | Declares purpose, required columns, sample size preconditions, output schema, assumptions, and interpretation limits for analytical/statistical computations. |
| **Tenant Access & Publication** | [`src/platform/publication.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/platform/publication.py) | Enforces tenant boundaries at read and cache borders, provides controlled versioned sharing to authorized recipient tenants, and supports instant revocation. |
| **Inspection CLI & API** | [`platform_cli/main.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/platform_cli/main.py), [`src/sharing/api.py`](file:///c:/Users/taher/Documents/StatSEO-fSport/src/sharing/api.py) | Structured CLI commands (`events`, `plan`, `methods`) and HTTP inspection endpoints (`/methods`, `/quarantine`, `/publications`). |

---

## 3. Data and Event Flow

```mermaid
graph TD
    A[Synthetic Generator / Ingest Emitter] -->|Event Envelope| B[Event Broker / Topic]
    B -->|Consume Batch| C[Event Consumer]
    C -->|Validate Schema| D{Valid?}
    D -->|No| E[Quarantine Store (SQLite)]
    D -->|Yes| F{Processed Before?}
    F -->|Yes (Duplicate)| G[Skip Write & Advance Offset]
    F -->|No| H[Write to Bronze Delta Table (Tenant Partitioned)]
    H --> I[Mark Processed & Commit Checkpoint]
    
    subgraph Asset DAG & Selective Recomputation
        H --> J[Bronze Asset]
        J --> K[Task: Clean / Transform]
        K --> L[Silver Asset]
        L --> M[Task: Analytical Method / Statistical Engine]
        M --> N[Gold Asset Materialization]
    end
    
    N -->|Controlled Publish| O[Published Data Product]
    O -->|Authorize Tenant| P[Authorized Read-Only API]
```

---

## 4. Concepts Disambiguation

| Concept | Purpose | Identifier Format | Example |
|---|---|---|---|
| **Event ID** | Logical identity of an incoming domain event for deduplication | `evt_<uuid>` | `evt_3eb13b90-4668-5257-bdd6-40fb06671ad1` |
| **Broker Offset / Checkpoint** | Progress position in a stream topic partition | Monotonic integer | `offset: 20` |
| **Input Fingerprint** | Cryptographic hash of input asset content or versions | SHA-256 hex | `c0b10a72ae01ffd6ce3ec380956f07ef...` |
| **Cache Key** | Unique identity of computation (tenant, inputs, task version, params, schema) | SHA-256 hex | `8fae1ce4f53d0b006659c94a3fd1240a...` |
| **Materialization ID** | Concrete recorded run output instance | `mat_<uuid>` | `mat_448f72eb3a10` |

---

## 5. Verification Commands & Test Results

### Baseline vs Current Test Summary

- Baseline (Milestone A): 179 passed, 3 xpassed (182 total)
- Current (Milestones A–G): **200 passed, 3 xpassed (203 total)**
- Regressions: **0**

### Commands to Run

```powershell
# 1. Run all unit and integration tests
.\.venv\Scripts\python.exe -m pytest --tb=short

# 2. Run synthetic event streaming CLI
.\.venv\Scripts\python.exe -m platform_cli events generate --count 10 --seed 42
.\.venv\Scripts\python.exe -m platform_cli events ingest --count 20 --seed 42

# 3. Inspect analytical method registry
.\.venv\Scripts\python.exe -m platform_cli methods list

# 4. Inspect dependency execution plan with cache keys
.\.venv\Scripts\python.exe -m platform_cli plan --assets gold_evidence --tenant tenant_alpha
```
