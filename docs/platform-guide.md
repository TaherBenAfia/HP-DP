# Platform guide

Reference for the enterprise analytics platform: layers, contracts,
statistical evidence, action scoring, sharing and dashboards.

## 1. Configuration

`configs/platform.yaml` is validated by `src/platform/config.py`
(`PlatformConfig`). Sections: `paths`, `statistics`, `visibility`,
`action_engine`, `sharing`, `data_products`. `${VAR:-default}` placeholders
are expanded from the environment at load time; the config is cached
(`load_platform_config.cache_clear()` in tests).

Data sources are declared in `configs/datasources/`:

* `sports_seo.json` — the contract: primary key `(url, query)`, 256
  attributes with per-attribute `origin`, `present_in_source`, dtype and
  nullability. 213 attributes come from the source CSV, 43 are produced by
  the platform (SVTP, trends, action scores).
* `registry.json` — dataset id → contract file mapping.

`src/platform/datasource_registry.py` resolves checksums, validates the
source against the contract and appends to an ingestion log.

## 2. Bronze and Silver

* **Bronze** (`src/ingestion/`): byte-stable copy of the source + metadata
  (SHA-256 checksum, row count, ingestion run id). Idempotent — an unchanged
  checksum skips; `ingest start --force` rebuilds.
* **Silver** (`src/transforms/`): typed columns, normalized names, null
  normalization, duplicate removal, per-row quality score and a quarantine
  table for rows failing key rules (34 rows: `primary_key_query_not_null`,
  `query_not_blank`).

## 3. Domain adapter

`src/domains/base.py` defines `DomainContext` / `DomainAdapter`. A domain
provides: feature registry, action families, statistics inputs, metrics and
a `run()` pipeline. Domains are discovered through `_DOMAINS` and selected
per dataset (`domain_for_dataset`).

The sports SEO domain (`src/domains/sports_seo/`):

| File | Role |
| --- | --- |
| `features.py` | 114 declared features in 7 families + outcome |
| `actions.py` | 6 `ActionFamily` definitions (weights, rules, recommendations) |
| `schema.py` | analysis-frame validation (Pandera) + validity mask |
| `statistics.py` | candidate features, evidence build, significance filter |
| `metrics.py` | entity/query/page metrics + summary tables |
| `visibility.py` (generic) | SVTP: rank, demand, entity components |
| `pipeline.py` | orchestration + publication + manifests + lineage |

### SVTP visibility model

`src/analytics/visibility.py`:

* `rank_score = (max_pos - pos + 1) / max_pos` — normalized per query.
* `rank_group` = 1-3 / 4-6 / 7-10, `rank_group_robust` = top/bottom half.
* `page_trend` = balanced ordinal trend (top half of ranks ⇒ 1).
* `demand_component_available = false`: the demand column is neutral 0.5 and
  recorded as unavailable in metadata.
* `svtp_score` = weighted mean of *available* components only
  (weights in `config.visibility`).

## 4. Statistical evidence

`src/statistics/engine.py` runs three tests per candidate feature:

* **Spearman** rank association with `svtp_score` (outcome).
* **Mann–Whitney U** for binary `page_trend` (rank-biserial effect size).
* **Chi-square** for categorical features (Cramér's V; direction = share of
  the reference level in class 1 vs class 0).

Benjamini–Hochberg FDR (`alpha = 0.05`, per test family) produces `q_value`
and `significant_fdr`. Skipped features (constant, too few observations,
missing column) are kept in the table with `status = "skipped"` so the
absence of evidence is visible. Published columns: `feature`, `family`,
`outcome`, `test`, `n`, `statistic`, `p_value`, `effect_size`,
`effect_size_type`, `q_value`, `significant_fdr`, `direction`, `status`,
`notes`.

Current run: 174 tests (64 Spearman / 64 Mann-Whitney / 44 chi-square),
93 significant after FDR.

## 5. Action engine

`src/analytics/action_engine.py`:

1. **Deficits** — every feature rule yields a deficit in `[0, 1]`:
   `binary` (desired value absent ⇒ 1), `range` (distance outside the
   healthy band), `percentile` (position in the observed distribution).
   Constant features are skipped (they carry no signal).
2. **Evidence weight** — `w_eff = weight × (0.5 + 0.5 × support)` where
   `support = min(|effect| / 0.5, 1)` for FDR-significant features,
   otherwise 0. Evidence re-weights; it never invents a deficit.
3. **Family score** — weighted mean of deficits for that family.
4. **Decision columns** — `primary_action` (largest score),
   `primary_action_score`, `action_priority` (high ≥ 0.66, medium ≥ 0.33),
   `evidence_features` (JSON: feature/weight/deficit/support/q_value),
   `reason`, `recommended_action`.

Distribution of the current run: schema_commerce 847, entity_authority 785,
metadata_optimization 256, content_enrichment 77, internal_linking 33,
technical_seo 0.

## 6. Gold products

12 Delta tables under `data/gold/`: `entity_metrics`, `query_metrics`,
`page_metrics`, `statistical_evidence`, `action_recommendations`,
`action_distribution`, `action_summary`, `visibility_summary`,
`rank_group_summary`, `data_quality`, `quality_summary`,
`sports_seo/observations`.

Publication writes:

* row counts, source checksum, product version (`configs/platform.yaml`),
* JSON manifests to `sharing/manifests/<dataset>/`,
* lineage records (table → parents → source checksum) to
  `data/gold/lineage`.

Lineage chain: `gold/action_recommendations → silver/sports_seo →
gold/statistical_evidence → bronze/sports_seo`.

## 7. Querying

`src/query/engine.py` opens DuckDB and registers `delta_scan` views for
every published table (plus bronze/silver/quarantine):

```bash
python -m platform_cli query tables
python -m platform_cli query "SELECT * FROM statistical_evidence
                              WHERE significant_fdr ORDER BY q_value LIMIT 10"
```

## 8. Sharing layer

* `src/sharing/manifests.py` — manifest discovery/inspection.
* `src/sharing/oidc.py` — `TokenValidator`: RS256 via JWKS (Keycloak-style
  issuer), HS256 with `SHARING_JWT_SECRET`, or an open local mode while
  `sharing.require_auth` is false. Scope check: `analytics:read`.
* `src/sharing/api.py` — read-only FastAPI app:
  `GET /health`, `/whoami`, `/tables`, `/tables/{name}?limit=`,
  `/lineage/{table}`, `/manifests`. Unknown tables ⇒ 404, no token ⇒ 401,
  wrong scope ⇒ 403, GET-only.

```bash
uvicorn src.sharing.api:app --port 8080
curl -H "Authorization: Bearer $TOKEN" localhost:8080/tables/action_recommendations?limit=5
```

## 9. Dashboard

`streamlit run app.py` — 7 pages (Overview, Actions, Statistics, Entities,
Queries, Observations, Lineage) backed by the same Gold tables, with a
persistent research-integrity disclaimer and a drill-down into each row's
`evidence_features` JSON.

## 10. Orchestration

`flows/` (Prefect 3): tasks for ingest, validate, silver, analytics and a
quality gate (valid-row share ≥ threshold); flows `ingest`, `silver-to-gold`,
`daily_etl`. Run `python -m flows.pipeline_flow` for a single execution or
`python -m flows.scheduled` for the schedule. Telemetry can be disabled
with `PREFECT_TELEMETRY_ENABLED=false`.

## 11. Testing

| Suite | Scope |
| --- | --- |
| `tests/unit/` | visibility model, statistics engine, action engine |
| `tests/platform/` | config, env expansion, contracts, registry, checksums |
| `tests/integration/` | Bronze→Gold pipeline, FDR discipline, lineage, manifests, query engine, API + auth |
| legacy DVDCF tests | `tests/test_*.py` (crawler framework) |

```bash
ruff check .   # clean
pytest         # 168 passed
```
