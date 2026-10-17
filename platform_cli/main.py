"""CLI implementation.

Usage::

    python -m platform_cli <command> [--id | --datasource DATASET]

Commands
--------
domain      describe the registered domain plugins
datasource  list / info / validate registered datasets
ingest      read the raw source into Bronze
silver      normalise and validate Bronze into Silver
analytics   run features, statistics, actions and publish Gold
stats       summary of the published statistical evidence
query       run SQL against the published Delta tables
lineage     trace a published table back to its source
share       list or inspect sharing manifests
lakehouse   publish / list Iceberg lakehouse tables
governance  evaluate policy-as-code rules
drift       compare current Silver against the drift reference
run         ingest + silver + analytics in one step
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import polars as pl

from src.platform.config import PlatformConfig, load_platform_config
from src.platform.datasource_registry import DatasourceRegistry

DEFAULT_DATASET = "sports_seo_tunisia_v1"


# -- helpers ---------------------------------------------------------------
def _config(args: argparse.Namespace) -> PlatformConfig:
    return load_platform_config(getattr(args, "config", None))


def _dataset(args: argparse.Namespace) -> str:
    return getattr(args, "datasource", None) or getattr(args, "id", None) or DEFAULT_DATASET


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _registry(args: argparse.Namespace) -> tuple[PlatformConfig, DatasourceRegistry]:
    cfg = _config(args)
    return cfg, DatasourceRegistry(cfg)


# -- domain ----------------------------------------------------------------
def cmd_domain(args: argparse.Namespace) -> int:
    from src.domains import available_domains, domain_for_dataset

    if args.action == "list":
        _print_json({"domains": available_domains()})
        return 0
    adapter = domain_for_dataset(_dataset(args))
    _print_json(adapter.describe())
    return 0


# -- datasource ------------------------------------------------------------
def cmd_datasource(args: argparse.Namespace) -> int:
    cfg, registry = _registry(args)
    if args.action == "list":
        entries = []
        for dataset_id in registry.ids():
            contract = registry.get(dataset_id)
            entries.append(
                {
                    "dataset_id": dataset_id,
                    "domain": contract.domain,
                    "version": contract.version,
                    "path": str(registry.resolve_path(contract).relative_to(cfg.repo_root)),
                    "source_exists": registry.exists(contract),
                }
            )
        _print_json({"datasources": entries})
        return 0

    dataset_id = _dataset(args)
    if args.action == "info":
        contract = registry.get(dataset_id)
        payload = contract.model_dump(mode="json")
        payload["checksum"] = registry.checksum(contract)
        payload["physical_path"] = str(
            registry.resolve_path(contract).relative_to(cfg.repo_root)
        )
        _print_json(payload)
        return 0

    if args.action == "validate":
        report = registry.validate(dataset_id)
        print(report.render())
        return 0 if report.passed else 2
    return 1


# -- pipeline stages -------------------------------------------------------
def cmd_ingest(args: argparse.Namespace) -> int:
    from src.ingestion.pipeline import ingest

    result = ingest(_dataset(args), force=args.force)
    _print_json(
        {
            "run_id": result.run_id,
            "dataset_id": result.dataset_id,
            "status": result.status,
            "row_count": result.row_count,
            "bronze_table": result.bronze_table,
            "checksum": result.source_checksum,
        }
    )
    return 0 if result.status in {"success", "skipped"} else 1


def cmd_silver(args: argparse.Namespace) -> int:
    from src.transforms.sports_seo import build_silver

    result = build_silver(_dataset(args))
    _print_json(result.as_dict())
    return 0


def cmd_analytics(args: argparse.Namespace) -> int:
    from src.domains.sports_seo.pipeline import run as run_pipeline

    result = run_pipeline(_dataset(args))
    _print_json(result.report)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    dataset_id = _dataset(args)
    statuses: dict[str, Any] = {}

    from src.ingestion.pipeline import ingest

    ingestion = ingest(dataset_id)
    statuses["ingest"] = ingestion.status
    if ingestion.status == "failed":
        _print_json(statuses)
        return 1

    from src.transforms.sports_seo import build_silver

    silver = build_silver(dataset_id)
    statuses["silver"] = silver.as_dict()

    from src.domains.sports_seo.pipeline import run as run_pipeline

    analytics = run_pipeline(dataset_id)
    statuses["analytics"] = analytics.report
    _print_json(statuses)
    return 0


# -- inspection ------------------------------------------------------------
def cmd_stats(args: argparse.Namespace) -> int:
    from src.platform.storage import Storage

    storage = Storage(_config(args))
    evidence = storage.read(storage.gold_table("statistical_evidence"))
    if evidence is None or evidence.is_empty():
        print("no statistical evidence published yet; run `analytics run`")
        return 1

    ok = evidence.filter(pl.col("status") == "ok")
    by_test = (
        ok.group_by("test")
        .agg(
            pl.len().alias("tests"),
            pl.col("significant_fdr").sum().alias("significant"),
            pl.col("q_value").median().alias("median_q"),
        )
        .sort("tests", descending=True)
    )
    print("Statistical evidence (FDR: Benjamini-Hochberg alpha="
          f"{_config(args).statistics.fdr_alpha}):")
    print(by_test)

    significant = ok.filter(pl.col("significant_fdr")).sort(
        ["test", "q_value", "effect_size"], descending=[False, False, False]
    )
    print(f"\nFDR-significant results: {significant.height}")
    print(
        significant.select(
            [
                "feature",
                "test",
                "outcome",
                "n",
                "effect_size",
                "effect_size_type",
                "q_value",
                "direction",
            ]
        ).head(args.top)
    )

    skipped = evidence.filter(pl.col("status") != "ok")
    if skipped.height:
        print(f"\nSkipped features: {skipped.height}")
        print(skipped.select(["feature", "notes"]))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from src.query import QueryEngine

    with QueryEngine(_config(args)) as engine:
        if args.sql.strip().lower() in {"tables", "--tables", "list"}:
            for name in engine.tables():
                print(name)
            return 0
        frame = engine.run(args.sql, limit=args.limit)
        print(frame)
        print(f"\n{frame.height} row(s)", file=sys.stderr)
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    from src.platform.lineage import LineageStore

    store = LineageStore(_config(args))
    if args.action == "list":
        records = store.records()
        print(records.select(["table", "layer", "run_id", "row_count", "produced_at"]))
        return 0
    print(store.describe(args.table))
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    cfg = _config(args)
    manifests = cfg.resolve(cfg.paths.sharing_manifest_dir)
    if args.action == "list":
        found = sorted(manifests.glob("*/*.json"))
        rows = []
        for path in found:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "product": path.stem,
                    "table": payload.get("table", ""),
                    "dataset_id": payload.get("dataset_id", ""),
                    "version": payload.get("product_version", ""),
                    "rows": payload.get("row_count", 0),
                    "generated_at": payload.get("generated_at", ""),
                    "manifest": str(path.relative_to(cfg.repo_root)),
                }
            )
        _print_json({"manifests": rows})
        return 0

    path = manifests / _dataset(args) / f"{args.product}.json"
    if not path.exists():
        print(f"no manifest for product {args.product!r}")
        return 1
    _print_json(json.loads(path.read_text(encoding="utf-8")))
    return 0


# -- events ----------------------------------------------------------------
def cmd_events(args: argparse.Namespace) -> int:
    from src.generators.synthetic import SyntheticEventGenerator
    from src.ingestion.broker import InMemoryBroker
    from src.ingestion.event_consumer import EventConsumer
    from src.platform.metadata_store import MetadataStore

    cfg = _config(args)
    meta = MetadataStore(cfg.resolve("data/metadata.db"))

    tenants = (
        [t.strip() for t in args.tenants.split(",")]
        if getattr(args, "tenants", None)
        else ["tenant_alpha", "tenant_beta"]
    )
    gen = SyntheticEventGenerator(
        seed=args.seed,
        tenants=tenants,
        duplicate_rate=args.duplicate_rate,
        malformed_rate=args.malformed_rate,
    )
    events = gen.generate_batch(count=args.count)

    if args.action == "generate":
        _print_json(
            {
                "generated_count": len(events),
                "sample_event": events[0] if events else None,
                "tenants": tenants,
            }
        )
        return 0

    if args.action == "ingest":
        broker = InMemoryBroker()
        topic = "events"
        broker.publish_batch(topic, events)
        consumer = EventConsumer(
            broker=broker,
            metadata_store=meta,
            bronze_base_dir=cfg.resolve("data/bronze/events"),
        )
        report = consumer.process_topic(topic=topic, batch_size=args.count)
        _print_json(report.model_dump(mode="json"))
        return 0
    return 1


# -- methods ---------------------------------------------------------------
def cmd_methods(args: argparse.Namespace) -> int:
    from src.analytics.method_registry import create_default_method_registry

    registry = create_default_method_registry()
    _print_json({"methods": [m.model_dump() for m in registry.list_methods()]})
    return 0


# -- plan ------------------------------------------------------------------
def cmd_plan(args: argparse.Namespace) -> int:
    from src.platform.asset_graph import AssetGraph, DataAsset, TaskDefinition
    from src.platform.metadata_store import MetadataStore
    from src.platform.planner import ExecutionPlanner

    cfg = _config(args)
    meta = MetadataStore(cfg.resolve("data/metadata.db"))
    graph = AssetGraph()

    graph.register_asset(DataAsset(asset_id="bronze_events", name="Bronze Events", layer="bronze"))
    graph.register_asset(DataAsset(asset_id="silver_cleaned", name="Silver Cleaned", layer="silver"))
    graph.register_asset(DataAsset(asset_id="gold_features", name="Gold Features", layer="gold"))
    graph.register_asset(DataAsset(asset_id="gold_evidence", name="Gold Statistical Evidence", layer="gold"))

    graph.register_task(
        TaskDefinition(
            task_id="clean_events",
            name="Clean Events",
            input_assets=["bronze_events"],
            output_assets=["silver_cleaned"],
        )
    )
    graph.register_task(
        TaskDefinition(
            task_id="engineer_features",
            name="Engineer Features",
            input_assets=["silver_cleaned"],
            output_assets=["gold_features"],
        )
    )
    graph.register_task(
        TaskDefinition(
            task_id="statistical_evidence",
            name="Statistical Evidence",
            input_assets=["gold_features"],
            output_assets=["gold_evidence"],
        )
    )

    requested = (
        [a.strip() for a in args.assets.split(",")]
        if getattr(args, "assets", None)
        else ["gold_evidence"]
    )
    tenant = getattr(args, "tenant", "tenant_alpha")

    planner = ExecutionPlanner(graph=graph, metadata_store=meta)
    plan = planner.plan(requested_assets=requested, tenant_id=tenant)
    _print_json(plan.model_dump(mode="json"))
    return 0


# -- lakehouse -------------------------------------------------------------
def cmd_lakehouse(args: argparse.Namespace) -> int:
    from src.lakehouse.tables import list_published_tables, publish_all

    cfg = _config(args)
    if args.action == "publish":
        published = publish_all(cfg)
        _print_json({"namespace": cfg.lakehouse.namespace, "published": published})
        return 0
    _print_json({"namespace": cfg.lakehouse.namespace, "tables": list_published_tables(cfg)})
    return 0


# -- governance ------------------------------------------------------------
def cmd_governance(args: argparse.Namespace) -> int:
    from src.governance.policies import enforce

    report = enforce(_config(args))
    print(report.render())
    return 0 if report.passed else 2


# -- drift -----------------------------------------------------------------
def cmd_drift(args: argparse.Namespace) -> int:
    from src.observability.drift import run_drift_check

    report = run_drift_check(_config(args))
    if report is None:
        print("drift reference initialised; run again to compare")
        return 0
    _print_json(report.model_dump())
    return 0 if report.passed else 2


# -- parser ----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m platform_cli",
        description="Enterprise analytics platform: Bronze -> Silver -> Gold -> sharing",
    )
    parser.add_argument("--config", default=None, help="path to platform.yaml")
    dataset_parent = argparse.ArgumentParser(add_help=False)
    dataset_parent.add_argument(
        "--datasource", "--id", dest="datasource", default=None,
        help=f"dataset id (default: {DEFAULT_DATASET})",
    )

    parser.add_argument("--version", action="store_true", help="print platform version")

    sub = parser.add_subparsers(dest="command")

    domain = sub.add_parser("domain", parents=[dataset_parent], help="domain plugins")
    domain.add_argument("action", choices=["describe", "list"], nargs="?", default="describe")
    domain.set_defaults(func=cmd_domain)

    datasource = sub.add_parser("datasource", parents=[dataset_parent], help="dataset registry")
    datasource.add_argument(
        "action", choices=["list", "info", "validate"], nargs="?", default="list"
    )
    datasource.set_defaults(func=cmd_datasource)

    ingest = sub.add_parser("ingest", parents=[dataset_parent], help="ingest into Bronze")
    ingest.add_argument("action", nargs="?", const="start", default="start")
    ingest.add_argument(
        "--force", action="store_true", help="re-ingest even if the checksum is unchanged"
    )
    ingest.set_defaults(func=cmd_ingest)

    silver = sub.add_parser("silver", parents=[dataset_parent], help="build Silver")
    silver.add_argument("action", nargs="?", const="build", default="build")
    silver.set_defaults(func=cmd_silver)

    analytics = sub.add_parser("analytics", parents=[dataset_parent], help="publish Gold")
    analytics.add_argument("action", nargs="?", const="run", default="run")
    analytics.set_defaults(func=cmd_analytics)

    run = sub.add_parser("run", parents=[dataset_parent], help="ingest + silver + analytics")
    run.add_argument("action", nargs="?", const="all", default="all")
    run.set_defaults(func=cmd_run)

    stats = sub.add_parser("stats", help="statistical evidence summary")
    stats.add_argument("action", nargs="?", const="summary", default="summary")
    stats.add_argument("--top", type=int, default=15)
    stats.set_defaults(func=cmd_stats)

    query = sub.add_parser("query", help="SQL over the published Delta tables")
    query.add_argument("sql", help="SQL statement, or 'tables' to list views")
    query.add_argument("--limit", type=int, default=None)
    query.set_defaults(func=cmd_query)

    lineage = sub.add_parser("lineage", parents=[dataset_parent], help="lineage inspection")
    lineage.add_argument("action", choices=["trace", "list"], nargs="?", default="trace")
    lineage.add_argument("table", nargs="?", default="gold/action_recommendations")
    lineage.set_defaults(func=cmd_lineage)

    share = sub.add_parser("share", parents=[dataset_parent], help="sharing manifests")
    share.add_argument("action", choices=["list", "inspect"], nargs="?", default="list")
    share.add_argument("product", nargs="?", default="action_recommendations")
    share.set_defaults(func=cmd_share)

    events = sub.add_parser("events", help="synthetic event streaming & ingestion")
    events.add_argument("action", choices=["generate", "ingest"], nargs="?", default="generate")
    events.add_argument("--count", type=int, default=50, help="number of events")
    events.add_argument("--seed", type=int, default=42, help="deterministic seed")
    events.add_argument("--tenants", type=str, default="tenant_alpha,tenant_beta", help="comma-separated tenants")
    events.add_argument("--duplicate-rate", type=float, default=0.05, help="duplicate injection rate")
    events.add_argument("--malformed-rate", type=float, default=0.05, help="malformed injection rate")
    events.set_defaults(func=cmd_events)

    methods = sub.add_parser("methods", help="inspect analytical method registry")
    methods.add_argument("action", nargs="?", const="list", default="list")
    methods.set_defaults(func=cmd_methods)

    plan = sub.add_parser("plan", help="inspect dependency execution plan")
    plan.add_argument("--assets", type=str, default="gold_evidence", help="comma-separated requested asset IDs")
    plan.add_argument("--tenant", type=str, default="tenant_alpha", help="target tenant ID")
    plan.set_defaults(func=cmd_plan)

    lakehouse = sub.add_parser("lakehouse", help="Iceberg lakehouse publish/inspect")
    lakehouse.add_argument("action", choices=["publish", "tables"], nargs="?", default="tables")
    lakehouse.set_defaults(func=cmd_lakehouse)

    governance = sub.add_parser("governance", help="policy-as-code governance checks")
    governance.add_argument("action", choices=["check"], nargs="?", default="check")
    governance.set_defaults(func=cmd_governance)

    drift = sub.add_parser("drift", help="Evidently drift check against the reference")
    drift.add_argument("action", choices=["check"], nargs="?", default="check")
    drift.set_defaults(func=cmd_drift)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point returning a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        print(load_platform_config().platform.model_dump_json(indent=2))
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
