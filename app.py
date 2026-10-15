"""Enterprise analytics dashboard (Streamlit).

Run with::

    streamlit run app.py

The dashboard only *reads* published Gold products. Every screen repeats the
research-integrity note: results are observational, the SVTP is a proxy for
visibility (not Search Console), and action scores are transparent heuristics.
"""

from __future__ import annotations

import json
from functools import lru_cache

import polars as pl
import streamlit as st

from src.platform.config import load_platform_config
from src.platform.lineage import LineageStore
from src.platform.storage import Storage

DISCLAIMER = (
    "Observational analytics: associations are not causal effects. "
    "svtp_score is a Search Visibility Trend Proxy built from SERP positions "
    "(no click, impression or Google Trends data is used), and action scores "
    "are documented heuristics, not guarantees."
)

st.set_page_config(
    page_title="Enterprise Analytics Platform",
    page_icon=":bar_chart:",
    layout="wide",
)

CONFIG = load_platform_config()
STORAGE = Storage(CONFIG)


@lru_cache(maxsize=32)
def load_table(name: str) -> pl.DataFrame | None:
    """Read a published Delta table (cached for the session)."""
    table = (
        STORAGE.gold_domain_table("sports_seo", name)
        if name == "observations"
        else STORAGE.gold_table(name)
    )
    return STORAGE.read(table)


def safe(name: str) -> pl.DataFrame:
    """Read a table or explain that it is not published yet."""
    frame = load_table(name)
    if frame is None or not frame.height:
        st.info(f"`{name}` is not published yet - run `python -m platform_cli analytics run`.")
        return pl.DataFrame()
    return frame


def header(title: str, note: str = "") -> None:
    """Page heading plus the integrity note."""
    st.title(title)
    if note:
        st.caption(note)
    st.warning(DISCLAIMER)


# -- overview --------------------------------------------------------------
def page_overview() -> None:
    header("Platform overview")

    quality = safe("quality_summary")
    visibility = safe("visibility_summary")
    actions = safe("action_summary")

    left, right = st.columns(2)
    if quality.height:
        metrics = {row["metric"]: row["value"] for row in quality.iter_rows(named=True)}
        with left:
            st.subheader("Data quality")
            st.metric("Silver rows", int(metrics.get("silver_rows", 0)))
            st.metric("Valid rows", int(metrics.get("valid_rows", 0)))
            st.metric("Quarantined rows", int(metrics.get("invalid_rows", 0)))
            st.metric("Constant (zero-variance) columns", int(metrics.get("constant_columns", 0)))
    if visibility.height:
        values = {row["metric"]: row["value"] for row in visibility.iter_rows(named=True)}
        with right:
            st.subheader("Visibility (SVTP)")
            st.metric("Observations", int(values.get("observations", 0)))
            st.metric("Mean svtp_score", f"{values.get('svtp_mean', 0):.3f}")
            st.metric("Median SERP position", f"{values.get('mean_serp_position', 0):.2f}")
            st.metric("Top-3 share", f"{values.get('top3_share', 0):.1%}")

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Primary action distribution")
        if actions.height:
            st.bar_chart(actions.to_pandas().set_index("primary_action")["observations"])
    with col2:
        st.subheader("SERP bands")
        groups = safe("rank_group_summary")
        if groups.height:
            st.bar_chart(groups.to_pandas().set_index("rank_group")["observations"])

    st.divider()
    st.subheader("Data product manifests")
    manifests = sorted((CONFIG.resolve(CONFIG.paths.sharing_manifest_dir)).glob("*/*.json"))
    rows = []
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "product": path.stem,
                "rows": payload.get("row_count"),
                "version": payload.get("product_version"),
                "generated_at": payload.get("generated_at"),
            }
        )
    if rows:
        st.dataframe(pl.DataFrame(rows), width="stretch")


# -- actions ---------------------------------------------------------------
def page_actions() -> None:
    header(
        "Action recommendations",
        "Deficit-based scoring over six explicit families; statistical evidence "
        "only re-weights feature contributions.",
    )
    frame = safe("action_recommendations")
    if not frame.height:
        return

    c1, c2, c3 = st.columns(3)
    families = sorted(frame["primary_action"].unique().to_list())
    with c1:
        selected = st.multiselect("Action family", families, default=families)
    with c2:
        priorities = sorted(frame["action_priority"].unique().to_list())
        chosen_priority = st.multiselect("Priority", priorities, default=priorities)
    with c3:
        entities = sorted(frame["entity"].unique().to_list())
        chosen_entity = st.selectbox("Entity", ["(all)"] + entities)

    filtered = frame.filter(
        pl.col("primary_action").is_in(selected)
        & pl.col("action_priority").is_in(chosen_priority)
    )
    if chosen_entity != "(all)":
        filtered = filtered.filter(pl.col("entity") == chosen_entity)

    st.write(f"{filtered.height} recommendation(s)")
    st.dataframe(
        filtered.select(
            [
                "entity",
                "query",
                "url",
                "serp_position",
                "primary_action",
                "primary_action_score",
                "action_priority",
                "secondary_action",
                "recommended_action",
            ]
        ).sort("primary_action_score", descending=True),
        width="stretch",
        height=320,
    )

    row = st.selectbox(
        "Inspect a recommendation",
        range(filtered.height),
        format_func=lambda i: f"{filtered['entity'][i]} | {filtered['query'][i]}",
    )
    if filtered.height:
        record = filtered.row(row, named=True)
        st.markdown(f"**Recommended action:** {record['recommended_action']}")
        st.markdown(f"**Reason:** {record['reason']}")
        st.markdown("**Evidence (top contributors):**")
        try:
            evidence = json.loads(record["evidence_features"])
            st.dataframe(pl.DataFrame(evidence), width="stretch")
        except (TypeError, json.JSONDecodeError):
            st.write(record["evidence_features"])
        with st.expander("Per-family scores"):
            score_columns = [c for c in filtered.columns if c.endswith("_action_score")]
            st.json({c: round(float(record[c]), 4) for c in score_columns})


# -- statistics ------------------------------------------------------------
def page_statistics() -> None:
    header(
        "Statistical evidence",
        "Spearman, Mann-Whitney U and chi-square with Benjamini-Hochberg FDR "
        "correction per test family.",
    )
    frame = safe("statistical_evidence")
    if not frame.height:
        return

    c1, c2 = st.columns(2)
    tests = sorted(frame["test"].unique().to_list())
    with c1:
        chosen_tests = st.multiselect("Test", tests, default=tests)
    with c2:
        only_significant = st.toggle("FDR-significant only", value=True)

    filtered = frame.filter(pl.col("test").is_in(chosen_tests))
    if only_significant:
        filtered = filtered.filter(pl.col("status") == "ok", pl.col("significant_fdr"))
    filtered = filtered.sort("q_value")

    st.write(f"{filtered.height} result(s)")
    st.dataframe(
        filtered.select(
            [
                "feature",
                "family",
                "test",
                "outcome",
                "n",
                "effect_size",
                "effect_size_type",
                "p_value",
                "q_value",
                "significant_fdr",
                "direction",
            ]
        ),
        width="stretch",
        height=320,
    )

    numeric = filtered.filter(pl.col("effect_size").is_not_null())
    if numeric.height:
        st.subheader("Effect sizes")
        st.bar_chart(
            numeric.to_pandas().set_index("feature")["effect_size"].head(40)
        )

    skipped = frame.filter(pl.col("status") != "ok")
    if skipped.height:
        with st.expander(f"Skipped features ({skipped.height})"):
            st.dataframe(skipped.select(["feature", "notes"]), width="stretch")


# -- entities / queries ----------------------------------------------------
def _dimension_page(name: str, title: str, key: str) -> None:
    header(title)
    frame = safe(name)
    if not frame.height:
        return
    top = st.slider("Rows to show", 5, min(100, frame.height), 15)
    st.dataframe(
        frame.sort("observations", descending=True).head(top),
        width="stretch",
    )
    metric = "svtp_score_mean"
    if metric in frame.columns:
        st.subheader("Mean svtp_score")
        chart = (
            frame.sort("observations", descending=True)
            .head(top)
            .select([key, metric])
            .to_pandas()
            .set_index(key)
        )
        st.bar_chart(chart)


def page_entities() -> None:
    _dimension_page("entity_metrics", "Entity metrics", "entity")


def page_queries() -> None:
    _dimension_page("query_metrics", "Query metrics", "query")


def page_observations() -> None:
    header("Observations (Gold detail table)")
    frame = safe("observations")
    if not frame.height:
        return
    entities = sorted(frame["entity"].unique().to_list())
    entity = st.selectbox("Entity", ["(all)"] + entities)
    subset = frame if entity == "(all)" else frame.filter(pl.col("entity") == entity)
    st.dataframe(
        subset.select(
            [
                c
                for c in (
                    "entity",
                    "query",
                    "url",
                    "serp_position",
                    "svtp_score",
                    "page_trend",
                    "primary_action",
                    "primary_action_score",
                    "action_priority",
                )
                if c in subset.columns
            ]
        ),
        width="stretch",
        height=400,
    )


# -- lineage ---------------------------------------------------------------
def page_lineage() -> None:
    header("Lineage", "Every Gold product traces back to the original source file.")
    store = LineageStore(CONFIG, STORAGE)
    records = store.records()
    if not records.height:
        st.info("No lineage recorded yet.")
        return
    tables = sorted(records["table"].to_list())
    table = st.selectbox("Table", tables)
    chain = store.trace(table)
    for step in chain:
        st.markdown(
            f"`{step['table']}` - **{step['layer']}** - run `{step['run_id']}` - "
            f"{step['row_count']} rows"
        )
        if step.get("source_checksum"):
            st.caption(
                f"source: {step['source_path']} - sha256 {step['source_checksum'][:16]}"
            )
        st.divider()


PAGES = {
    "Overview": page_overview,
    "Actions": page_actions,
    "Statistics": page_statistics,
    "Entities": page_entities,
    "Queries": page_queries,
    "Observations": page_observations,
    "Lineage": page_lineage,
}


def main() -> None:
    """Render the selected page."""
    st.sidebar.title("Enterprise Analytics")
    st.sidebar.caption(f"{CONFIG.platform.name} v{CONFIG.platform.version}")
    choice = st.sidebar.radio("Pages", list(PAGES))
    st.sidebar.divider()
    st.sidebar.caption(
        "Data products are read from local Delta tables; "
        "no cloud service is required."
    )
    PAGES[choice]()


if __name__ == "__main__":
    main()
