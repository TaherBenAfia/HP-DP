"""Typed platform configuration loaded from ``configs/platform.yaml``.

Configuration over hard-coding: no module in the platform embeds a path or a
threshold that belongs in this file. Environment variables of the form
``${VAR:-default}`` are expanded so that local secrets never live in Git.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upwards until ``configs/platform.yaml`` is found.

    Falls back to the repository root inferred from this file's location
    (``<root>/src/platform/config.py``).
    """
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "configs" / "platform.yaml").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR:-default}`` placeholders."""
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default or "")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


class CsvOptions(BaseModel):
    """CSV reader defaults; a datasource contract may override them."""

    delimiter: str = ","
    encoding: str = "utf-8"
    null_values: list[str] = Field(default_factory=lambda: ["", "NA", "N/A", "null", "None"])


class Paths(BaseModel):
    """All platform storage locations, relative to the repository root."""

    raw_dir: str = "data/raw"
    bronze_dir: str = "data/bronze"
    bronze_metadata_dir: str = "data/bronze/_metadata"
    bronze_manifest_dir: str = "data/bronze/_manifests"
    silver_dir: str = "data/silver"
    quarantine_dir: str = "data/silver/quarantine"
    gold_dir: str = "data/gold"
    duckdb_path: str = "data/analytics.duckdb"
    datasources_dir: str = "configs/datasources"
    datasource_registry: str = "configs/datasources/registry.json"
    sharing_manifest_dir: str = "sharing/manifests"


class IngestionConfig(BaseModel):
    """Ingestion behaviour."""

    csv: CsvOptions = Field(default_factory=CsvOptions)
    idempotent: bool = True


class SilverConfig(BaseModel):
    """Silver layer behaviour."""

    drop_duplicates: bool = True
    quarantine_invalid_rows: bool = True


class QualityConfig(BaseModel):
    """Data-quality behaviour."""

    quarantine_severities: list[str] = Field(default_factory=lambda: ["error"])


class StatisticsConfig(BaseModel):
    """Statistical engine behaviour."""

    fdr_method: str = "benjamini-hochberg"
    fdr_alpha: float = 0.05
    min_observations: int = 30
    interpretation: str = "associational"


class VisibilityConfig(BaseModel):
    """Search Visibility Trend Proxy configuration."""

    demand_component_available: bool = False
    demand_neutral_value: float = 0.5
    weights: dict[str, float] = Field(
        default_factory=lambda: {"rank": 0.5, "entity": 0.5, "demand": 0.0}
    )


class ActionEngineConfig(BaseModel):
    """Action engine behaviour."""

    priority_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"high": 0.66, "medium": 0.33}
    )
    families: list[str] = Field(default_factory=list)


class DataProductConfig(BaseModel):
    """Version stamps applied to published Gold data products."""

    version: str = "1.0.0"
    schema_version: str = "1.0.0"


class SharingConfig(BaseModel):
    """Data-sharing / OIDC behaviour."""

    oidc_issuer: str = "http://localhost:8080/realms/enterprise"
    audience: str = "enterprise-analytics"
    token_ttl_seconds: int = 300
    read_only: bool = True
    require_auth: bool = False
    jwks_url: str = ""
    jwt_secret: str = ""
    read_scope: str = "analytics:read"
    admin_scope: str = "analytics:admin"


class PlatformMeta(BaseModel):
    """Platform identity."""

    name: str = "enterprise-analytics-platform"
    version: str = "1.0.0"
    pipeline_version: str = "1.0.0"
    storage_backend: str = "filesystem"


class LakehouseCatalogConfig(BaseModel):
    """Iceberg catalog settings. ``type`` selects a local SQLite catalog or a
    REST catalog such as Apache Polaris."""

    type: str = "local"
    uri: str = "sqlite:///data/lakehouse/catalog.db"
    warehouse: str = "data/lakehouse/warehouse"
    rest_uri: str = ""
    rest_token: str = ""
    credential: str = ""


class LakehouseConfig(BaseModel):
    """Open Iceberg/Polaris storage layer."""

    enabled: bool = True
    namespace: str = "statseo"
    catalog: LakehouseCatalogConfig = Field(default_factory=LakehouseCatalogConfig)
    publish_tables: list[str] = Field(
        default_factory=lambda: [
            "data/silver/sports_seo",
            "data/gold/statistical_evidence",
            "data/gold/action_recommendations",
            "data/gold/visibility_summary",
        ]
    )


class TemporalConfig(BaseModel):
    """Temporal worker settings. An empty ``address`` means activities run
    in-process without a Temporal server (used by tests and local runs)."""

    address: str = ""
    namespace: str = "default"
    task_queue: str = "statseo"


class DagsterConfig(BaseModel):
    """Dagster job settings."""

    job_name: str = "seo_pipeline"


class OrchestrationConfig(BaseModel):
    """Streaming + batch orchestration behaviour."""

    temporal: TemporalConfig = Field(default_factory=TemporalConfig)
    dagster: DagsterConfig = Field(default_factory=DagsterConfig)


class ConformalConfig(BaseModel):
    """Split-conformal correction applied to proxy-based effect estimates."""

    alpha: float = 0.1
    train_fraction: float = 0.5
    min_train: int = 30


class CausalConfig(BaseModel):
    """Causal core estimation behaviour. Estimates carry explicit
    assumptions and are never presented as design-based guarantees."""

    methods: list[str] = Field(
        default_factory=lambda: ["dowhy_linear", "econml_linear_dml", "doubleml_plr"]
    )
    alpha: float = 0.05
    min_observations: int = 50
    conformal: ConformalConfig = Field(default_factory=ConformalConfig)


class GovernanceConfig(BaseModel):
    """Policy-as-code and signed-artifact behaviour."""

    policies_path: str = "configs/policies.yaml"
    signing_key_path: str = "configs/keys/artifact_signing.key"
    require_signatures: bool = True


class OpenLineageConfig(BaseModel):
    """OpenLineage event emission."""

    enabled: bool = True
    transport: str = "file"
    file_path: str = "logs/openlineage/events.jsonl"
    namespace: str = "statseo_local"


class DriftConfig(BaseModel):
    """Evidently-based drift detection between Silver snapshots."""

    enabled: bool = True
    reference_path: str = "data/silver/_baseline/silver_reference.parquet"
    report_dir: str = "logs/drift"
    drift_share_threshold: float = 0.3


class ObservabilityConfig(BaseModel):
    """End-to-end lineage emission and drift detection."""

    openlineage: OpenLineageConfig = Field(default_factory=OpenLineageConfig)
    drift: DriftConfig = Field(default_factory=DriftConfig)


class FeatureStoreConfig(BaseModel):
    """Feast + LanceDB feature store behaviour."""

    repo_path: str = "feature_repo"
    offline_dir: str = "data/features/offline"
    online_db: str = "data/features/lancedb"
    entity: str = "page_id"


class SemanticConfig(BaseModel):
    """SQLMesh semantic-layer behaviour."""

    project_path: str = "sqlmesh_project"
    gateway: str = "localduck"
    state_path: str = "data/sqlmesh/state"


class OlapConfig(BaseModel):
    """ClickHouse OLAP sink (optional; disabled unless a URL is provided)."""

    enabled: bool = False
    url: str = "${CLICKHOUSE_URL:-}"
    database: str = "statseo"
    username: str = "${CLICKHOUSE_USER:-default}"
    password: str = "${CLICKHOUSE_PASSWORD:-}"
    table_prefix: str = ""


class RecommendConfig(BaseModel):
    """DSPy + LangGraph recommendation behaviour. An empty ``lm_model``
    selects the deterministic offline LM used by tests."""

    lm_model: str = "${DSPY_LM_MODEL:-}"
    graph_state_path: str = "logs/recommend/graph_state.json"


class PlatformConfig(BaseModel):
    """Root configuration model for the whole platform."""

    platform: PlatformMeta = Field(default_factory=PlatformMeta)
    paths: Paths = Field(default_factory=Paths)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    silver: SilverConfig = Field(default_factory=SilverConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    statistics: StatisticsConfig = Field(default_factory=StatisticsConfig)
    visibility: VisibilityConfig = Field(default_factory=VisibilityConfig)
    action_engine: ActionEngineConfig = Field(default_factory=ActionEngineConfig)
    data_products: DataProductConfig = Field(default_factory=DataProductConfig)
    sharing: SharingConfig = Field(default_factory=SharingConfig)
    lakehouse: LakehouseConfig = Field(default_factory=LakehouseConfig)
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    causal: CausalConfig = Field(default_factory=CausalConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    feature_store: FeatureStoreConfig = Field(default_factory=FeatureStoreConfig)
    semantic: SemanticConfig = Field(default_factory=SemanticConfig)
    olap: OlapConfig = Field(default_factory=OlapConfig)
    recommend: RecommendConfig = Field(default_factory=RecommendConfig)
    logging: dict[str, Any] = Field(default_factory=dict)

    repo_root: Path = Field(default_factory=find_repo_root)

    model_config = {"arbitrary_types_allowed": True}

    # -- convenience -------------------------------------------------------
    def resolve(self, relative: str | Path) -> Path:
        """Resolve a repo-relative path to an absolute path."""
        path = Path(relative)
        return path if path.is_absolute() else (self.repo_root / path)


@lru_cache(maxsize=4)
def load_platform_config(path: str | None = None) -> PlatformConfig:
    """Load and validate ``configs/platform.yaml`` (cached per path)."""
    config_path = Path(path) if path else find_repo_root() / "configs" / "platform.yaml"
    if not config_path.exists():
        return PlatformConfig()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return PlatformConfig.model_validate(_expand_env(raw))
