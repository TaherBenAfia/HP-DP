"""Local DuckDB analytics database.

The platform is local-first: DuckDB reads the published Delta tables in place
(``delta_scan``) and exposes them as ordinary SQL views, so every Gold product
is queryable without copying data into another store.
"""

from __future__ import annotations

from typing import Any

import duckdb
import polars as pl

from src.platform.config import PlatformConfig, load_platform_config
from src.platform.storage import Storage

DEFAULT_VIEWS = [
    "entity_metrics",
    "query_metrics",
    "page_metrics",
    "action_distribution",
    "action_summary",
    "visibility_summary",
    "rank_group_summary",
    "statistical_evidence",
    "action_recommendations",
    "data_quality",
    "quality_summary",
    "lineage",
]


class QueryEngine:
    """Thin wrapper around a DuckDB connection with Delta views registered."""

    def __init__(self, config: PlatformConfig | None = None) -> None:
        self.config = config or load_platform_config()
        self.storage = Storage(self.config)
        self.connection = duckdb.connect(str(self.storage.duckdb_path()))
        self._register_all()

    # -- registration ------------------------------------------------------
    def _delta_scan(self, table: Any) -> str:
        return f"delta_scan('{table.as_posix()}')"

    def register(self, name: str, table: Any) -> bool:
        """Create or replace the view ``name`` over a Delta table."""
        if not self.storage.exists(table):
            return False
        self.connection.execute(
            f'CREATE OR REPLACE VIEW "{name}" AS SELECT * FROM {self._delta_scan(table)}'
        )
        return True

    def _register_all(self) -> list[str]:
        registered: list[str] = []
        for name in DEFAULT_VIEWS:
            if self.register(name, self.storage.gold_table(name)):
                registered.append(name)
        if self.register("observations", self.storage.gold_domain_table("sports_seo", "observations")):
            registered.append("observations")
        if self.register("bronze_sports_seo", self.storage.bronze_table("sports_seo")):
            registered.append("bronze_sports_seo")
        if self.register("silver_sports_seo", self.storage.silver_table("sports_seo")):
            registered.append("silver_sports_seo")
        if self.register("quarantine_sports_seo", self.storage.quarantine_table("sports_seo")):
            registered.append("quarantine_sports_seo")
        self.registered = registered
        return registered

    # -- queries -----------------------------------------------------------
    def run(self, sql: str, limit: int | None = None) -> pl.DataFrame:
        """Execute SQL and return a Polars frame (optionally limited)."""
        query = sql.strip().rstrip(";")
        if limit and query.lower().startswith(("select", "with", "describe", "explain")):
            query = f"SELECT * FROM ({query}) LIMIT {int(limit)}"
        return pl.from_arrow(self.connection.execute(query).arrow())

    def tables(self) -> list[str]:
        """Views currently available for querying."""
        rows = self.connection.execute("SHOW TABLES").fetchall()
        return sorted(str(row[0]) for row in rows)

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()

    def __enter__(self) -> QueryEngine:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def list_tables(config: PlatformConfig | None = None) -> list[str]:
    """Names of the registered Gold/layer views."""
    with QueryEngine(config) as engine:
        return engine.tables()


def run_query(sql: str, config: PlatformConfig | None = None, limit: int | None = None) -> pl.DataFrame:
    """Convenience helper: register views, run SQL, return a frame."""
    with QueryEngine(config) as engine:
        return engine.run(sql, limit=limit)
