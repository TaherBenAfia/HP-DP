# HANDOFF — HyperPersonalized SEO Data Pipeline (`feature/lakehouse-v2`)

> **DO NOT COMMIT / MERGE / PUSH** unless explicitly told. All work is uncommitted in the
> working tree. This file itself is untracked.

Last session: built + verified 6 new subsystems on top of the existing medallion platform.
Full test suite exits **0** (green). Remaining work is CLI wiring + docs cleanup + final lint/type pass.

---

## 0) RESUME PROMPT (copy-paste this on next launch)

```
Continue the HyperPersonalized SEO Data Pipeline on branch feature/lakehouse-v2.
Read HANDOFF.md first for full context. Do NOT commit/merge/push anything.

Task: finish the remaining integration work:
1. Full verification pass: `uv run pytest -o addopts="" --tb=short` (confirm green + get exact counts),
   `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/` — fix anything new.
3. Wire CLI commands into platform_cli/main.py (argparse): `lakehouse publish|tables`,
   `governance check`, `drift check` — reuse PlatformConfig/load_platform_config.
   Ensure `uv run python -m platform_cli run all` still works.
4. Docs stack cleanup: README.md stack section, docker-compose.yml (remove prefect service),
   .env.example (remove PREFECT_* vars), docs/platform-guide.md + docs/architecture/* if they list the stack.
5. Re-validate .github/workflows/ci.yml YAML and the pytest-summary action path.
6. Write the final summary report (what changed, test counts, how to run). Do NOT commit/push.
```

---

## 1) OBJECTIVE

Build the **"HyperPersonalized SEO Data Pipeline"** (governance-aware, evidence-native lakehouse)
from the spec, on a new branch, reusing the existing codebase:

- Medallion ingest (Bronze→Silver→Gold) — already exists
- **New:** Apache Iceberg + Polaris (`src/lakehouse/`)
- **New:** Dagster + Temporal orchestration (`src/orchestration/`)
- **New:** Causal inference DoWhy/EconML/DoubleML + conformal (`src/causal/`)
- **New:** Policy-as-code governance + Ed25519 signing (`src/governance/`)
- **New:** OpenLineage + Evidently drift (`src/observability/`)
- **New:** Feast + LanceDB feature store (`src/feature_store/`), SQLMesh semantic mesh (`src/semantic/`), ClickHouse OLAP (`src/olap/`)
- **New:** DSPy + LangGraph recommendations (`src/recommend/`)

Existing stack: DuckDB (`QueryEngine`), Polars, Delta Lake (`deltalake`), pandera/pydantic contracts, custom lineage, SVTP visibility proxy.

---

## 2) ENVIRONMENT / CONVENTIONS

- Repo: `C:\Users\taher\Documents\StatSEO-fSport`
- Windows PowerShell 5.1 — **no `&&`** (use `;` / `if ($?) {}`), no `sed`/`python3`, use `uv run ...`.
- Venv Python: **3.13.14**. Package manager: `uv` (0.11.6).
- **CI runs Python 3.12** (`.github/workflows/ci.yml`, ubuntu-latest) and does:
  `uv sync --all-extras` → `uv run ruff check --fix .` → `uv run python -m platform_cli run all`
  → `uv run pytest --junitxml=pytest-results.xml` → `python .github/actions/pytest-summary.py` → mypy (non-failing).
  → New deps MUST stay importable/py3.12-compatible.
- `pyproject.toml [tool.pytest.ini_options]`: `addopts = "-q"`, `testpaths = ["tests"]`.
  NOTE: `-q` hides the final "N passed" line when piped; use `-o addopts=""` to see counts.
- Tests build config directly: `PlatformConfig(repo_root=tmp_path, <Section>Config(...))`; `.resolve("rel")` resolves under `tmp_path`.

---

## 3) DONE THIS SESSION

### Branch
- Created `feature/lakehouse-v2` from `main`. Everything below is **uncommitted**.

### Dependency / config changes
- `pyproject.toml`: added extra **`lakehouse`**, **removed `prefect`**.
  `uv sync --all-extras` succeeded. All 14 new packages importable:
  pyiceberg 0.12.0, sqlmesh 0.236.3, dagster 1.13.26, temporalio 1.34.0, dowhy 0.14,
  econml 0.17.0, doubleml 0.11.4, evidently 0.7.23, openlineage-python 1.x, feast 0.67.0,
  lancedb 0.40.0, clickhouse_connect 1.11.0, dspy 3.4.1, langgraph 0.2+.
- `src/platform/config.py`: extended with typed sections (all defaulted, backward compatible):
  `lakehouse`, `orchestration.temporal`/`.dagster`, `causal`, `governance`, `observability.openlineage`/`.drift`,
  `feature_store`, `semantic`, `olap`, `recommend`.
- `configs/platform.yaml`: values for all new sections.
- `configs/policies.yaml`: **created** — 7 policies (bronze_immutable, quarantine_preserved,
  fdr_discipline, no_causal_claims, gold_lineage_present, signed_artifacts, demand_component_neutral).
- `.gitignore`: added data/lakehouse, data/features, data/sqlmesh, feature_repo data, .dagster/.temporal.

### New modules + tests (all delivered by parallel agents)
| Area | Files | Tests |
|------|-------|-------|
| Lakehouse | `src/lakehouse/{catalog,tables,readers}.py` | 11 passed |
| Orchestration | `src/orchestration/{dagster_defs,temporal_defs}.py` | passed |
| Causal | `src/causal/{estimators,conformal,core}.py` | 16 passed |
| Governance + Observability | `src/governance/{policies,signing}.py`, `src/observability/{lineage_ol,drift}.py` | 19 passed |
| Feature store + Semantic + OLAP | `src/feature_store/{lancedb_store,repo}.py`, `src/semantic/mesh.py`, `src/olap/clickhouse.py` | 20 passed, 1 skipped |
| Recommend | `src/recommend/{dspy_modules,graph}.py` | 12 passed, 1 skipped |

### CI fix (working tree)
- `.github/workflows/ci.yml`: moved inline `python -c "..."` pytest-summary to
  `.github/actions/pytest-summary.py` (fixes YAML error at old line 38). YAML re-validated with `yaml.safe_load`.
- `.github/actions/pytest-summary.py`: **created** (new untracked dir).

### Bug fixed (existing code, was blocking full suite)
- `src/sharing/oidc.py`: pyjwt **2.15.1** ignores `audience`/`issuer`/`leeway` when nested inside the
  `options` dict → raised `InvalidAudienceError` → API returned 401.
  Fix: pass `audience=`, `issuer=`, `leeway=` as **keyword args** to `pyjwt.decode`; `options` keeps only `{"require": ["exp"]}`.
  (`tests/integration/test_query_and_api.py::test_auth_enforced_when_configured` now passes.)
  NOTE: pyjwt 2.15.1 was already pinned at `HEAD:uv.lock`, so this was a latent bug that would fail CI too.

### Verification so far
- `uv run pytest -q` → **exit 0** (green). Exact count not captured yet (the `-q` addopts hides summary).
  Approx new tests ≈ 78, 2 skipped; existing suite ≈ 91–113.

---

## 4) KEY API NOTES / GOTCHAS (learned, keep for next session)

- **pyiceberg 0.12**: use `pyiceberg.catalog.rest.RestCatalog` (alias `RESTCatalog`), not `RESTCatalog`/`append_table`.
  Append/overwrite via `catalog.load_table(id).append(df)` / `.overwrite(df)`.
  `SqlCatalog` warehouse layout: `<warehouse>/<namespace>/<table>/data/*.parquet`.
  RESTCatalog is **not lazy** — mock `_fetch_config`/`_refresh_token` in tests.
- **dspy**: dummy LM is `dspy.utils.dummies.DummyLM` (not `dspy.DummyLM`).
- **OpenLineage**: `FileConfig(log_file_path=..., append=True)` required for stable JSONL.
- **Evidently 0.7**: `from evidently import Report`, `from evidently.presets import DataDriftPreset`,
  parse `snapshot.dict()["metrics"]`.
- **doubleml**: `pval` (not `pvalues`). **econml**: must pass `X=None` (empty array raises). **dowhy 0.14**: no `p_value`.
- **pyjwt 2.15.1**: `audience`/`issuer`/`leeway` MUST be kwargs to `decode`, never inside `options`.

### Cross-module contracts (lazy imports)
- `src.lakehouse.tables.publish_all(config) -> dict`
- `src.observability.lineage_ol.OpenLineageEmitter(config).emit_start/emit_complete`
- `src.governance.signing.sign_payload(payload, private_key_pem) -> dict` / `verify_payload` / `load_or_create_keypair(path) -> (priv, pub)`
- `src.governance.policies.evaluate_policies(config)`
- `src.causal.conformal.split_conformal_intervals(...)`
- `src.recommend.dspy_modules.recommend(...)`

### Reusable existing helpers
- `src.platform.config.PlatformConfig` / `load_platform_config()` (lru_cached)
- `src.platform.storage.Storage(config)` (read/write/exists/history/gold_table/silver_table)
- `src.platform.lineage.LineageStore`
- `src.query.engine.QueryEngine` (DuckDB over Delta) + `DEFAULT_VIEWS`
- Pipeline entrypoints: `src.ingestion.pipeline.ingest`, `src.transforms.sports_seo.build_silver`,
  `src.domains.sports_seo.pipeline.run`; `DEFAULT_DATASET = "sports_seo_tunisia_v1"`.
- Gold tables include: entity_metrics, page_metrics, query_metrics, statistical_evidence,
  action_recommendations, visibility_summary, data_quality, lineage, …

---

## 5) TODO (next session)

- [ ] **Full verification**: `uv run pytest -o addopts="" --tb=short` (confirm green + exact counts).
- [ ] **Lint/format/types**: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/` — fix new issues in the new modules.
- [ ] **CLI integration** in `platform_cli/main.py` (argparse): add `lakehouse publish|tables`,
      `governance check`, `drift check` subcommands wired to `load_platform_config()`. Keep `run all` working.
- [ ] **Docs / stack cleanup**: README.md stack section, `docker-compose.yml` (remove **prefect** service),
      `.env.example` (remove `PREFECT_*`), `docs/platform-guide.md`, `docs/architecture/*` if they list the stack.
      Also update stale test counts in docs (README says "168 tests", DVDCF_README says "101").
- [ ] **Re-validate** `.github/workflows/ci.yml` (YAML) + `.github/actions/pytest-summary.py` path.
- [ ] **Final report** to user (changes, counts, how to run). **No commit/push.**

---

## 6) GIT STATUS SNAPSHOT (uncommitted)

```
 M .github/workflows/ci.yml
 M .gitignore
 M configs/platform.yaml
 M pyproject.toml
 M src/platform/config.py
 M src/sharing/oidc.py          # pyjwt audience fix
 M uv.lock
?? .github/actions/             # pytest-summary.py
?? configs/policies.yaml
?? src/{causal,feature_store,governance,lakehouse,observability,olap,orchestration,recommend,semantic}/
?? tests/{causal,feature_store,governance,lakehouse,observability,olap,orchestration,recommend,semantic}/
?? HANDOFF.md                   # this file
```

---

## 7) QUICK COMMANDS

```powershell
uv sync --all-extras
uv run pytest -o addopts="" --tb=short
uv run ruff check .
uv run ruff format --check .
uv run mypy src/
uv run python -m platform_cli run all
```
