"""Feast feature repository materialised from the local Gold lakehouse.

Gold Delta tables are exported to single Parquet files (the offline store
location Feast reads) and a small ``features.py`` module is generated with
entities, feature views and a feature service derived from the real Gold
schema. The configured entity key (``page_id``) is guaranteed to exist in every
exported Parquet: when a Gold table does not carry it, the column is derived
gracefully from the best available candidate (``url`` / ``entity`` / ``id``)
or, as a last resort, a stable row-index string.

Feast 0.67 removed the ``offline_store.base_dir`` key (file-backed offline
stores resolve data purely through ``FileSource.path``), so the offline
directory is injected into the generated ``FileSource`` paths instead.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import re
from pathlib import Path
from typing import Any

import polars as pl
from feast import (
    Entity,
    FeatureService,
    FeatureStore,
    FeatureView,
    OnDemandFeatureView,
)

from src.platform.config import PlatformConfig, load_platform_config
from src.platform.storage import Storage

DEFAULT_OFFLINE_TABLES = ["page_metrics", "visibility_summary", "action_recommendations"]

TIMESTAMP_FIELD = "event_timestamp"

_TIMESTAMP_CANDIDATES = ("event_timestamp", "generated_at", "event_time", "timestamp")

_ENTITY_FALLBACK_COLUMNS = ("url", "entity", "id")

_METADATA_COLUMNS = frozenset(
    {
        "product_id",
        "product_version",
        "source_dataset_version",
        "pipeline_version",
        "schema_version",
        "created_timestamp",
    }
)

_FEAST_TYPE_BY_DTYPE: dict[Any, str] = {
    pl.Int32: "Int32",
    pl.Int64: "Int64",
    pl.UInt32: "Int64",
    pl.UInt64: "Int64",
    pl.Float32: "Float32",
    pl.Float64: "Float64",
    pl.Boolean: "Bool",
    pl.String: "String",
    pl.Categorical: "String",
    pl.Enum: "String",
    pl.Date: "UnixTimestamp",
    pl.Datetime: "UnixTimestamp",
}

_VALUE_TYPE_BY_DTYPE: dict[Any, str] = {
    pl.Int32: "INT64",
    pl.Int64: "INT64",
    pl.UInt32: "INT64",
    pl.UInt64: "INT64",
    pl.Float32: "DOUBLE",
    pl.Float64: "DOUBLE",
    pl.Boolean: "BOOL",
    pl.String: "STRING",
    pl.Date: "UNIX_TIMESTAMP",
    pl.Datetime: "UNIX_TIMESTAMP",
}

_FeastType = str


def _offline_dir(config: PlatformConfig) -> Path:
    """Absolute directory holding the exported offline Parquet files."""
    return config.resolve(config.feature_store.offline_dir)


def _sample_schema(
    config: PlatformConfig, storage: Storage, table: str
) -> dict[str, Any] | None:
    """Return ``{column: polars dtype}`` for an exported or Gold table."""
    parquet = _offline_dir(config) / f"{table}.parquet"
    if parquet.exists():
        return dict(pl.scan_parquet(parquet).collect_schema())
    frame = storage.read(storage.gold_table(table))
    if frame is None:
        return None
    return dict(frame.schema)


def _feast_type(dtype: Any) -> _FeastType:
    """Map a Polars dtype to a Feast ``feast.types`` class name."""
    base = dtype.base_type() if hasattr(dtype, "base_type") else dtype
    return _FEAST_TYPE_BY_DTYPE.get(base, "String")


def _entity_value_type(dtype: Any | None) -> str:
    """Map a Polars dtype to a Feast ``ValueType`` member name."""
    if dtype is None:
        return "STRING"
    base = dtype.base_type() if hasattr(dtype, "base_type") else dtype
    return _VALUE_TYPE_BY_DTYPE.get(base, "STRING")


def _timestamp_field_for(schema: dict[str, Any]) -> str:
    """Pick the timestamp column Feast should use for a known schema."""
    for name in _TIMESTAMP_CANDIDATES:
        dtype = schema.get(name)
        if dtype is not None and dtype.base_type() == pl.Datetime:
            return name
    return TIMESTAMP_FIELD


def _ensure_event_timestamp(frame: pl.DataFrame) -> tuple[pl.DataFrame, str]:
    """Guarantee a datetime column exists; return the frame and its name."""
    for name in _TIMESTAMP_CANDIDATES:
        if name not in frame.columns:
            continue
        dtype = frame.schema[name]
        if dtype.base_type() == pl.Datetime:
            return frame, name
        if dtype == pl.String:
            parsed = frame.with_columns(
                pl.col(name).str.to_datetime(strict=False, time_unit="us")
            )
            if parsed[name].null_count() < len(parsed):
                return parsed, name
    stamp = dt.datetime.now(dt.UTC).replace(tzinfo=None, microsecond=0)
    frame = frame.with_columns(pl.Series(TIMESTAMP_FIELD, [stamp] * len(frame)))
    return frame, TIMESTAMP_FIELD


def _ensure_entity_column(frame: pl.DataFrame, entity_key: str) -> pl.DataFrame:
    """Guarantee ``entity_key`` exists, deriving it from the best candidate."""
    if entity_key in frame.columns:
        return frame.with_columns(pl.col(entity_key).cast(pl.String))
    for candidate in (*_ENTITY_FALLBACK_COLUMNS, entity_key):
        if candidate in frame.columns:
            return frame.with_columns(pl.col(candidate).cast(pl.String).alias(entity_key))
    return frame.with_columns(
        pl.Series(entity_key, [str(index) for index in range(len(frame))])
    )


def export_gold_to_parquet(
    config: PlatformConfig | None = None,
    tables: list[str] | None = None,
) -> list[Path]:
    """Export Gold Delta tables to Parquet files Feast can read offline.

    Only tables that exist are exported. Every exported frame gains the
    configured entity key (string) and a datetime column usable as the Feast
    ``timestamp_field``.
    """
    config = config or load_platform_config()
    storage = Storage(config)
    offline_dir = _offline_dir(config)
    offline_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for table in tables or DEFAULT_OFFLINE_TABLES:
        frame = storage.read(storage.gold_table(table))
        if frame is None:
            continue
        frame = _ensure_entity_column(frame, config.feature_store.entity)
        frame, _ = _ensure_event_timestamp(frame)
        destination = offline_dir / f"{table}.parquet"
        frame.write_parquet(destination)
        written.append(destination)
    return written


def _project_name(config: PlatformConfig) -> str:
    """Sanitised Feast project name derived from the platform name."""
    return re.sub(r"[^a-z0-9_]+", "_", config.platform.name.lower()).strip("_")


def _feature_store_yaml(config: PlatformConfig) -> str:
    """Render the ``feature_store.yaml`` for a local provider repo."""
    return (
        f"project: {_project_name(config)}\n"
        "registry: data/registry.db\n"
        "provider: local\n"
        "online_store:\n"
        "  type: sqlite\n"
        "  path: data/online.db\n"
        "offline_store:\n"
        "  type: file\n"
        "auth:\n"
        "  type: no_auth\n"
        "entity_key_serialization_version: 2\n"
    )


def _feature_fields(
    schema: dict[str, Any], entity_key: str, timestamp_field: str
) -> list[tuple[str, _FeastType]]:
    """Feature-view fields: all non-key, non-metadata columns with Feast types."""
    excluded = {entity_key, timestamp_field, *_METADATA_COLUMNS}
    return [
        (name, _feast_type(dtype))
        for name, dtype in schema.items()
        if name not in excluded
    ]


def _render_features_py(
    offline_dir: Path,
    entity_key: str,
    entity_value_type: str,
    views: list[dict[str, Any]],
) -> str:
    """Render the generated ``features.py`` module source."""
    used_types = sorted({feast_type for view in views for _, feast_type in view["fields"]})
    lines = [
        '"""Generated Feast feature definitions over exported Gold Parquet tables."""',
        "",
        "from __future__ import annotations",
        "",
        "import datetime as dt",
        "from pathlib import Path",
        "",
        "from feast import Entity, FeatureService, FeatureView, Field, FileSource, ValueType",
    ]
    if used_types:
        lines.append(f"from feast.types import {', '.join(used_types)}")
    lines.extend(
        [
            "",
            f"OFFLINE_DIR = Path(r\"{offline_dir.as_posix()}\")",
            "",
            f"{entity_key} = Entity(",
            f'    name="{entity_key}",',
            f'    join_keys=["{entity_key}"],',
            f"    value_type=ValueType.{entity_value_type},",
            ")",
            "",
        ]
    )
    for view in views:
        table = view["table"]
        lines.extend(
            [
                f"{table}_source = FileSource(",
                f'    name="{table}_source",',
                f'    path=str(OFFLINE_DIR / "{table}.parquet"),',
                f'    timestamp_field="{view["timestamp_field"]}",',
                ")",
                "",
                f"{table}_view = FeatureView(",
                f'    name="{table}",',
                f"    entities=[{entity_key}],",
                "    ttl=dt.timedelta(days=3650),",
                "    schema=[",
            ]
        )
        for field_name, feast_type in view["fields"]:
            lines.append(f'        Field(name="{field_name}", dtype={feast_type}),')
        lines.extend(
            [
                "    ],",
                "    online=True,",
                f"    source={table}_source,",
                ")",
                "",
            ]
        )
    if views:
        service_features = ", ".join(f"{view['table']}_view" for view in views)
        lines.extend(
            [
                "gold_features = FeatureService(",
                '    name="gold_features",',
                f"    features=[{service_features}],",
                ")",
                "",
            ]
        )
    return "\n".join(lines)


def build_feature_repo(config: PlatformConfig | None = None) -> Path:
    """Materialise a Feast repo (``feature_store.yaml`` + ``features.py``).

    Feature views are generated for every default Gold table for which a
    schema sample is available (exported Parquet first, then Gold Delta). The
    exported Parquet files always carry the configured entity key, so the
    generated entity joins against every view.
    """
    config = config or load_platform_config()
    repo_dir = config.resolve(config.feature_store.repo_path)
    (repo_dir / "data").mkdir(parents=True, exist_ok=True)
    storage = Storage(config)
    entity_key = config.feature_store.entity
    offline_dir = _offline_dir(config)

    views: list[dict[str, Any]] = []
    entity_dtype: Any | None = None
    for table in DEFAULT_OFFLINE_TABLES:
        schema = _sample_schema(config, storage, table)
        if not schema:
            continue
        timestamp_field = _timestamp_field_for(schema)
        fields = _feature_fields(schema, entity_key, timestamp_field)
        if not fields:
            continue
        if entity_dtype is None:
            entity_dtype = schema.get(entity_key)
        views.append(
            {
                "table": table,
                "timestamp_field": timestamp_field,
                "fields": fields,
            }
        )

    (repo_dir / "feature_store.yaml").write_text(
        _feature_store_yaml(config), encoding="utf-8"
    )
    (repo_dir / "features.py").write_text(
        _render_features_py(
            offline_dir,
            entity_key,
            _entity_value_type(entity_dtype),
            views,
        ),
        encoding="utf-8",
    )
    return repo_dir


def _collect_feast_objects(features_path: Path) -> list[Any]:
    """Import the generated module and gather its Feast objects."""
    spec = importlib.util.spec_from_file_location("statseo_feast_features", features_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import feature definitions from {features_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wanted = (Entity, FeatureView, OnDemandFeatureView, FeatureService)
    seen: set[str] = set()
    objects: list[Any] = []
    for value in vars(module).values():
        if isinstance(value, wanted):
            key = f"{type(value).__name__}:{value.name}"
            if key not in seen:
                seen.add(key)
                objects.append(value)
    return objects


def apply_repo(config: PlatformConfig | None = None) -> str:
    """Run ``feast apply`` programmatically; return a status string.

    Errors are captured and returned as text so importers never crash.
    """
    config = config or load_platform_config()
    repo_dir = config.resolve(config.feature_store.repo_path)
    try:
        features_path = repo_dir / "features.py"
        if not features_path.exists():
            build_feature_repo(config)
        objects = _collect_feast_objects(features_path)
        store = FeatureStore(repo_path=str(repo_dir))
        store.apply(objects)
        return f"applied {len(objects)} objects to feast project '{store.project}'"
    except Exception as error:
        return f"feast apply failed: {error}"


def get_online_features(
    config: PlatformConfig | None = None,
    entity_rows: list[dict[str, Any]] | None = None,
    features: list[str] | None = None,
) -> dict[str, Any]:
    """Fetch online features for ``entity_rows`` from the applied repo."""
    config = config or load_platform_config()
    repo_dir = config.resolve(config.feature_store.repo_path)
    store = FeatureStore(repo_path=str(repo_dir))
    response = store.get_online_features(
        features=features or [],
        entity_rows=entity_rows or [],
    )
    return response.to_dict()
