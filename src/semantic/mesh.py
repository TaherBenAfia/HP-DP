"""SQLMesh semantic-layer project over the local Gold lakehouse.

The project is deliberately offline: every model reads Gold parquet files
through DuckDB's ``read_parquet`` glob so the semantic layer never needs a
Delta extension, a network connection or a warehouse. The generated
``config.yaml`` configures a single DuckDB gateway named by
``config.semantic.gateway`` plus a DuckDB state/cache store under
``config.semantic.state_path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.platform.config import PlatformConfig, load_platform_config

_SCHEMA = "statseo"

_MODELS: dict[str, dict[str, str]] = {
    "gold_statistical_evidence": {
        "table": "statistical_evidence",
        "select": "SELECT\n  *\nFROM read_parquet('{glob}')",
    },
    "gold_action_recommendations": {
        "table": "action_recommendations",
        "select": "SELECT\n  *\nFROM read_parquet('{glob}')",
    },
    "metrics_visibility": {
        "table": "visibility_summary",
        "select": (
            "SELECT\n"
            "  metric,\n"
            "  avg(value) AS avg_value,\n"
            "  count(*) AS observation_count\n"
            "FROM read_parquet('{glob}')\n"
            "GROUP BY metric"
        ),
    },
}


def _model_glob(config: PlatformConfig, table: str) -> str:
    """Absolute parquet glob for a Gold product."""
    gold_dir = config.resolve(config.paths.gold_dir)
    return (gold_dir / table / "*.parquet").as_posix()


def _render_model(config: PlatformConfig, name: str) -> str:
    """Render one SQLMesh model file for a Gold product."""
    spec = _MODELS[name]
    select = spec["select"].format(glob=_model_glob(config, spec["table"]))
    return (
        "MODEL (\n"
        f"  name {_SCHEMA}.{name},\n"
        "  kind FULL\n"
        ");\n\n"
        f"{select}\n"
    )


def _config_yaml(config: PlatformConfig) -> str:
    """Render the SQLMesh project configuration."""
    gateway = config.semantic.gateway
    duckdb_path = config.resolve(config.paths.duckdb_path).as_posix()
    state_dir = config.resolve(config.semantic.state_path)
    state_db = (state_dir / "state.duckdb").as_posix()
    cache_dir = (state_dir / "cache").as_posix()
    return (
        "gateways:\n"
        f"  {gateway}:\n"
        "    connection:\n"
        "      type: duckdb\n"
        f"      database: {duckdb_path}\n"
        "    state_connection:\n"
        "      type: duckdb\n"
        f"      database: {state_db}\n"
        "    scheduler:\n"
        "      type: builtin\n"
        f"default_gateway: {gateway}\n"
        "model_defaults:\n"
        "  dialect: duckdb\n"
        f"cache_dir: {cache_dir}\n"
    )


def build_project(config: PlatformConfig | None = None) -> Path:
    """Write the SQLMesh project (``config.yaml`` + ``models/*.sql``).

    Parent directories for the gateway database, state database and cache are
    created so DuckDB can create its files without a pre-existing layout.
    """
    config = config or load_platform_config()
    project_dir = config.resolve(config.semantic.project_path)
    models_dir = project_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    config.resolve(config.paths.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
    state_dir = config.resolve(config.semantic.state_path)
    (state_dir / "cache").mkdir(parents=True, exist_ok=True)

    (project_dir / "config.yaml").write_text(_config_yaml(config), encoding="utf-8")
    for name in _MODELS:
        (models_dir / f"{name}.sql").write_text(
            _render_model(config, name), encoding="utf-8"
        )
    return project_dir


def load_models(config: PlatformConfig | None = None) -> dict[str, Any]:
    """Parse the project and return ``{model_name: SqlModel}``.

    ``Context`` construction parses every SQL model, so a syntax error raises
    here. The context is closed before returning to release DuckDB handles.
    """
    config = config or load_platform_config()
    project_dir = config.resolve(config.semantic.project_path)
    from sqlmesh.core.context import Context

    context = Context(paths=[str(project_dir)])
    try:
        return dict(context.models)
    finally:
        context.close()


def model_names(config: PlatformConfig | None = None) -> list[str]:
    """Return the parsed model names (fully qualified)."""
    return sorted(load_models(config))


def run_semantic_layer(
    config: PlatformConfig | None = None, environment: str = "dev"
) -> str:
    """Apply a plan and run every FULL model in ``environment``.

    Optional and network-free; returns a human-readable status summary.
    """
    config = config or load_platform_config()
    project_dir = build_project(config)
    from sqlmesh.core.context import Context

    context = Context(paths=[str(project_dir)])
    try:
        context.plan(environment, no_prompts=True, auto_apply=True)
        status = context.run(environment)
    finally:
        context.close()
    return f"sqlmesh environment '{environment}' planned and run (status: {status})"
