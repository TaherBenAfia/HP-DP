"""Generate the sports SEO datasource contract from the physical source file.

The contract is derived from the real file (columns + pandas dtypes + observed
nullability) so that it can never drift from the data it describes. Semantic
types and field-origin classes are assigned by explicit rules; the generated
JSON is committed as ``configs/datasources/sports_seo.json``.

Usage:
    python scripts/generate_contract.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "data" / "raw" / "sports_seo" / "sports_seo_page_features.csv"
OUTPUT = REPO_ROOT / "configs" / "datasources" / "sports_seo.json"

DATASET_ID = "sports_seo_tunisia_v1"

# ---------------------------------------------------------------------------
# Field-origin classes (see platform rule: derived fields must never be
# presented as raw source measurements).
# ---------------------------------------------------------------------------
NORMALIZED_FIELDS = {
    "brands",
    "domain",
    "path",
    "language",
    "language_declared",
    "charset_declared",
    "page_type",
    "content_type",
    "template_type",
    "query_intent",
    "query_category",
    "https",
    "https_status",
    "official_domain",
}

DERIVED_FIELDS = {
    "is_indexable",
    "canonical_present",
    "canonical_self_referencing",
    "robots_index",
    "robots_follow",
    "lexical_diversity",
    "content_depth",
    "text_to_html_ratio",
    "avg_sentence_length",
    "avg_paragraph_length",
    "heading_depth",
    "avg_heading_length",
    "alt_text_coverage",
    "avg_alt_text_length",
    "orphan_page",
    "in_degree",
    "out_degree",
    "internal_pagerank",
    "url_depth",
    "url_word_count",
    "url_has_keyword",
    "url_has_brand",
    "url_has_category",
    "url_has_product",
    "schema_completeness_score",
    "commerce_completeness_score",
    "trust_score",
    "brand_consistency_score",
    "semantic_coherence_score",
    "topical_authority_score",
    "topic_coverage_score",
    "embedding_dimension",
    "page_hierarchy_level",
    "click_depth",
    "brand_mentions_count",
    "topic_count",
    "entity_count",
    "named_entity_count",
    "sports_entity_count",
    "product_entity_count",
    "brand_entity_count",
    "query_word_count",
    "query_character_count",
    "in_stock",
}

DERIVED_PATTERNS = (
    "_score",
    "_gap",
    "_vs_competitors",
    "_similarity",
    "_coverage",
    "_ratio",
    "_present",
    "has_",
    "is_",
    "avg_",
    "_avg",
    "diversity",
    "_depth",
    "pagerank",
    "_degree",
    "relevance",
    "consistency",
    "coherence",
    "authority",
    "_norm",
    "_level",
)

# Fields the platform computes after ingestion (never present in the source).
PLATFORM_DERIVED: list[dict[str, object]] = [
    {
        "name": "brand_extraction_source",
        "dtype": "string",
        "origin": "NORMALIZED",
        "semantic_type": "entity",
        "description": "Mechanism used to derive the entity label from the source row.",
        "present_in_source": False,
    },
    {
        "name": "entity",
        "dtype": "string",
        "origin": "NORMALIZED",
        "semantic_type": "entity",
        "description": "Domain-derived entity label. Not necessarily a manufacturer brand.",
        "present_in_source": False,
    },
    {
        "name": "query_word_count_calc",
        "dtype": "int64",
        "origin": "DERIVED_FEATURE",
        "semantic_type": "query",
        "description": "Token count of the query string, recomputed by the platform.",
        "present_in_source": False,
    },
    {
        "name": "rank_group",
        "dtype": "string",
        "origin": "DERIVED_FEATURE",
        "semantic_type": "ranking",
        "description": "Banded SERP position group (for example 1-3, 4-6, 7-10).",
        "present_in_source": False,
    },
    {
        "name": "rank_group_robust",
        "dtype": "string",
        "origin": "DERIVED_FEATURE",
        "semantic_type": "ranking",
        "description": "Robust SERP position grouping used for sensitivity checks.",
        "present_in_source": False,
    },
    {
        "name": "rank_score",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "visibility",
        "description": "Normalised rank signal used by the visibility model.",
        "present_in_source": False,
    },
    {
        "name": "rank_score_norm",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "visibility",
        "description": "Rescaled rank signal in [0, 1].",
        "present_in_source": False,
    },
    {
        "name": "demand_score",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand component. Neutral while no usable Google Trends observations exist.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "demand_score_norm",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Rescaled demand component in [0, 1]; neutral (0.5) when unavailable.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "demand_score_adjusted",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Demand component after external adjustments. Unavailable in this dataset.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "demand_component_available",
        "dtype": "bool",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "False when no empirical temporal demand observations were available.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "entity_visibility",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "visibility",
        "description": "Entity-level visibility signal aggregated from its observations.",
        "present_in_source": False,
    },
    {
        "name": "entity_score_norm",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "visibility",
        "description": "Rescaled entity visibility signal in [0, 1].",
        "present_in_source": False,
    },
    {
        "name": "svtp_score",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "visibility",
        "description": "Search Visibility Trend Proxy: an externally observable rank/entity visibility proxy, not Search Console performance.",
        "present_in_source": False,
    },
    {
        "name": "page_trend",
        "dtype": "int64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "visibility",
        "description": "Balanced binary label derived from the SVTP distribution.",
        "present_in_source": False,
    },
    {
        "name": "trend_mean",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand summary. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_median",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand summary. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_recent",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand summary. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_historical",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand summary. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_growth",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand growth. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_slope",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand slope. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_volatility",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand volatility. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_peak",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand peak. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_min",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Temporal demand minimum. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_n_obs",
        "dtype": "int64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Count of usable demand observations. Zero in this dataset.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_level_norm",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Normalised demand level. Neutral while demand is unavailable.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_growth_norm",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Normalised demand growth. Neutral while demand is unavailable.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "trend_score",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Composite demand score. Neutral while demand is unavailable.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "related_trend_score",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Related-query demand score. Not populated: no usable Google Trends observations.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "tiktok_engagement_mean",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Social engagement signal. Optional external demand input.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "tiktok_engagement_max",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Social engagement signal. Optional external demand input.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "tiktok_video_count",
        "dtype": "int64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Social content volume signal. Optional external demand input.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "tiktok_adjustment",
        "dtype": "float64",
        "origin": "ANALYTICAL_OUTPUT",
        "semantic_type": "demand",
        "description": "Social adjustment factor applied to the demand component.",
        "present_in_source": False,
        "available": False,
    },
    {
        "name": "CONTENT_EXPANSION_REFRESH_action_score",
        "dtype": "float64",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Transparent prioritisation score for the content expansion/refresh action family.",
        "present_in_source": False,
    },
    {
        "name": "QUERY_PAGE_ALIGNMENT_action_score",
        "dtype": "float64",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Transparent prioritisation score for the query-page alignment action family.",
        "present_in_source": False,
    },
    {
        "name": "METADATA_SNIPPET_action_score",
        "dtype": "float64",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Transparent prioritisation score for the metadata/snippet action family.",
        "present_in_source": False,
    },
    {
        "name": "INTERNAL_AUTHORITY_action_score",
        "dtype": "float64",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Transparent prioritisation score for the internal authority action family.",
        "present_in_source": False,
    },
    {
        "name": "COMMERCE_SCHEMA_TRUST_action_score",
        "dtype": "float64",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Transparent prioritisation score for the commerce/schema/trust action family.",
        "present_in_source": False,
    },
    {
        "name": "TECHNICAL_INDEXABILITY_action_score",
        "dtype": "float64",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Transparent prioritisation score for the technical indexability action family.",
        "present_in_source": False,
    },
    {
        "name": "primary_action",
        "dtype": "string",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Highest scoring action family for the observation.",
        "present_in_source": False,
    },
    {
        "name": "primary_action_score",
        "dtype": "float64",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Score of the highest scoring action family.",
        "present_in_source": False,
    },
    {
        "name": "secondary_action",
        "dtype": "string",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Second highest scoring action family for the observation.",
        "present_in_source": False,
    },
    {
        "name": "action_priority",
        "dtype": "string",
        "origin": "DECISION_OUTPUT",
        "semantic_type": "decision",
        "description": "Heuristic priority band (High/Medium/Low). A prioritisation heuristic, not a causal claim.",
        "present_in_source": False,
    },
]

SEMANTIC_RULES: list[tuple[str, tuple[str, ...], str]] = [
    ("decision", ("action_score", "primary_action", "secondary_action", "action_priority"), ""),
    ("competitor_relative", ("_vs_competitors", "_gap"), ""),
    ("demand", ("trend_", "demand_", "tiktok_", "related_trend"), ""),
    ("visibility", ("svtp", "visibility", "rank_score", "page_trend", "entity_score"), ""),
    ("ranking", ("serp_position", "rank_group", "rank_"), ""),
    ("entity", ("brand_mentioned", "brand_mentions", "brand_in_", "brand_consistency",
                "official_domain", "brands", "entity", "named_entity", "sports_entity",
                "product_entity", "brand_entity", "embedding"), ""),
    ("semantic", ("similarity", "coverage", "semantic", "topic", "topical", "coherence",
                  "relevance", "lexical"), ""),
    ("metadata", ("title", "meta_description", "cta_", "alt_text", "h1_word", "h2_word", "h3_word"), ""),
    ("commerce", ("price", "stock", "cart", "sku", "product_", "size_", "color_", "shipping",
                  "payment", "warranty", "return_policy", "review", "rating", "offer",
                  "buy_now", "availability", "currency", "commerce", "model"), ""),
    ("schema", ("schema", "structured_data"), ""),
    ("trust", ("trust", "contact", "phone", "email", "address", "privacy", "terms",
               "about_page", "social_links", "author_information"), ""),
    ("link_graph", ("internal_link", "external_link", "anchor", "pagerank", "in_degree",
                    "out_degree", "orphan", "breadcrumb_links", "footer_links", "header_links",
                    "citation", "links_to_"), ""),
    ("technical", ("canonical", "robots", "https", "response_time", "page_size", "html_size",
                   "dom_element", "viewport", "mobile_meta", "hreflang", "charset", "url_depth",
                   "click_depth", "url_length", "hyphen", "query_parameter", "redirect",
                   "is_indexable", "page_hierarchy", "template_type", "language", "content_type",
                   "page_type", "url_"), ""),
    ("query", ("query",), ""),
    ("content", ("word_count", "char_count", "sentence", "paragraph", "heading", "h1", "h2",
                 "h3", "h4", "h5", "h6", "list", "table", "content", "image", "video",
                 "media", "text_to_html"), ""),
    ("identity", ("url", "domain", "path"), ""),
    ("observation", (), ""),
]

DESCRIPTION_HINTS = {
    "serp_position": "Observed organic SERP position (1-10 in the collected environment).",
    "url": "Canonical page URL as collected from the search result.",
    "query": "Search query that produced the observation.",
    "domain": "Registered domain of the page URL, normalised from the URL host.",
    "word_count": "Word count of the extracted main page text.",
    "internal_pagerank": "Internal PageRank estimate over the crawled link graph.",
    "content_depth": "Depth score derived from the page content structure.",
    "query_page_semantic_similarity": "Semantic similarity between the query and the page body.",
    "authority_gap": "Page authority minus the competitor benchmark for the same query.",
    "primary_action": "Action family with the highest transparent prioritisation score.",
    "action_priority": "Heuristic priority band derived from the prioritisation score.",
    "svtp_score": "Search Visibility Trend Proxy: observable rank/entity visibility proxy.",
}


def classify_origin(name: str) -> str:
    """Return the field-origin class for a source column."""
    if name in NORMALIZED_FIELDS:
        return "NORMALIZED"
    if name in DERIVED_FIELDS:
        return "DERIVED_FEATURE"
    if any(pat in name for pat in DERIVED_PATTERNS):
        return "DERIVED_FEATURE"
    if name.endswith("_score") or name.endswith("_gap"):
        return "DERIVED_FEATURE"
    return "RAW_SOURCE"


def classify_semantic(name: str) -> str:
    """Return the semantic type for a field."""
    for semantic_type, needles, _ in SEMANTIC_RULES:
        if any(needle in name for needle in needles):
            return semantic_type
    return "observation"


def pandas_dtype(dtype: object) -> str:
    """Map a pandas dtype to the contract dtype vocabulary."""
    return str(dtype)


def main() -> None:
    """Regenerate configs/datasources/sports_seo.json from the physical file."""
    frame = pd.read_csv(SOURCE, low_memory=False)
    primary_key = ["url", "query"]
    null_counts = frame.isna().sum()

    attributes: list[dict[str, object]] = []
    for column in frame.columns:
        dtype = pandas_dtype(frame[column].dtype)
        origin = classify_origin(column)
        semantic_type = classify_semantic(column)
        nullable = bool(null_counts[column] > 0) and column not in primary_key
        if column in primary_key:
            nullable = False
        attributes.append(
            {
                "name": column,
                "dtype": dtype,
                "nullable": nullable,
                "origin": origin,
                "semantic_type": semantic_type,
                "present_in_source": True,
                "description": DESCRIPTION_HINTS.get(
                    column, f"{semantic_type.replace('_', ' ')} attribute ({origin.lower()})."
                ),
            }
        )

    required_outputs = {
        "entity",
        "svtp_score",
        "page_trend",
        "primary_action",
        "primary_action_score",
        "secondary_action",
        "action_priority",
    }
    derived: list[dict[str, object]] = []
    for entry in PLATFORM_DERIVED:
        item = dict(entry)
        item.setdefault("nullable", str(item["name"]) not in required_outputs)
        derived.append(item)

    contract = {
        "dataset_id": DATASET_ID,
        "dataset_name": "Sports SEO Analytical Dataset",
        "domain": "sports_seo",
        "version": "1.0.0",
        "format": "csv",
        "path": "data/raw/sports_seo/sports_seo_page_features.csv",
        "primary_key": primary_key,
        "partition_columns": [],
        "classification": "internal",
        "description": (
            "Page-level SEO analytical feature table for Tunisian sports search demand: "
            "2,032 query-result observations across 213 page, query, semantic, link-graph, "
            "commerce, schema, trust, technical and competitor-relative attributes. "
            "First domain implementation of the enterprise analytics platform."
        ),
        "source_type": "external_web_analysis",
        "refresh_frequency": "manual",
        "schema_version": "1.0.0",
        "row_count_expected": int(len(frame)),
        "column_count_expected": int(len(frame.columns)),
        "validation": {
            "allow_extra_columns": True,
            "allow_missing_columns": False,
            "primary_key_must_be_unique": True,
        },
        "lineage": {
            "originating_project": "Mapping Sports Search Demand and Brand Visibility in Tunisia",
            "produced_by": "DVDCF feature engineering pipeline",
            "observation_type": "observational",
            "notes": [
                "SERP position is an observed outcome, not a manipulated variable.",
                "Domain-derived entity does not necessarily mean manufacturer brand.",
                "Statistical association must not be read as causation.",
            ],
        },
        "attributes": attributes + derived,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    source_attrs = [a for a in attributes if a["present_in_source"]]
    derived_attrs = [a for a in contract["attributes"] if not a["present_in_source"]]
    origins: dict[str, int] = {}
    for attr in attributes:
        origins[str(attr["origin"])] = origins.get(str(attr["origin"]), 0) + 1
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    print(f"source attributes : {len(source_attrs)}")
    print(f"platform-derived  : {len(derived_attrs)}")
    print(f"origin mix        : {origins}")


if __name__ == "__main__":
    main()
