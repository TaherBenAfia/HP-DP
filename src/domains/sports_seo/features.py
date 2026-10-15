"""Domain analytical features for the Tunisian sports SEO dataset.

Features are declared once here and consumed by every generic engine
(statistics, metrics, action scoring, dashboards). Direction conventions were
verified against the data (see ``scripts/validate_directions`` notes):

* ``*_gap`` = this page minus the competitor mean for the query
  (higher = better);
* ``*_relevance_gap`` in ``[-1, 0]`` for title/h1 (0 = perfect alignment);
* ``content_relevance_gap`` in ``[0, 1]`` behaves like a coverage score;
* technical cost columns (depth, size, response time, redirects) are
  lower-is-better.

Constant columns (``in_degree``, ``internal_pagerank``, ``authority_gap``,
``pagerank_vs_competitors``, ``semantic_similarity_vs_competitors``,
``semantic_relevance_gap``, ``has_offer_schema``, ``has_review_schema``, ...)
are intentionally absent: the engine skips them anyway, but a clean
declaration documents what is analytically usable.
"""

from __future__ import annotations

from src.analytics.feature_registry import FeatureKind, FeatureRegistry, FeatureRole, FeatureSpec

N = FeatureKind.NUMERIC
B = FeatureKind.BINARY
C = FeatureKind.CATEGORICAL

F = FeatureRole.FEATURE
OUT = FeatureRole.OUTCOME
K = FeatureRole.COMPETITOR

# (name, kind, role, higher_is_better, description)
_DECLARATIONS: dict[str, list[tuple[str, FeatureKind, FeatureRole, bool, str]]] = {
    "content_enrichment": [
        ("word_count", N, F, True, "words of visible text on the page"),
        ("char_count", N, F, True, "characters of visible text"),
        ("content_depth", N, F, True, "composite content depth score"),
        ("unique_word_count", N, F, True, "distinct words (vocabulary size)"),
        ("lexical_diversity", N, F, True, "type-token ratio"),
        ("topic_count", N, F, True, "detected topics on the page"),
        ("topic_coverage_score", N, F, True, "share of query topics covered"),
        ("topical_authority_score", N, F, True, "topic-level authority proxy"),
        ("semantic_coherence_score", N, F, True, "semantic coherence of body text"),
        ("heading_count", N, F, True, "total headings h1-h6"),
        ("h1_word_count", N, F, True, "words inside the H1"),
        ("alt_text_coverage", N, F, True, "share of images with alt text"),
        ("images_without_alt", N, F, False, "images missing alt text"),
        ("named_entity_count", N, F, True, "named entities extracted from the page"),
        ("entity_count", N, F, True, "entities detected on the page"),
        ("sports_entity_count", N, F, True, "sport-specific entities detected"),
        ("content_relevance_gap", N, F, True, "query-term coverage of the body text"),
        ("content_gap", N, K, True, "word count minus competitor mean for the query"),
        ("content_depth_vs_competitors", N, K, True, "depth relative to competitors"),
    ],
    "metadata_optimization": [
        ("title_present", B, F, True, "HTML title tag present"),
        ("title_length", N, F, True, "title length in characters (target 30-60)"),
        ("meta_description_present", B, F, True, "meta description present"),
        ("meta_description_length", N, F, True, "meta description length (target 50-160)"),
        ("keyword_in_title", B, F, True, "query keyword appears in the title"),
        ("keyword_in_meta_description", B, F, True, "keyword in the meta description"),
        ("cta_in_title", B, F, True, "call-to-action wording in the title"),
        ("cta_in_meta_description", B, F, True, "call-to-action wording in the description"),
        ("brand_in_title", B, F, True, "brand token in the title"),
        ("keyword_in_h1", B, F, True, "query keyword appears in the H1"),
        ("keyword_in_headings", B, F, True, "query keyword appears in any heading"),
        ("title_relevance_gap", N, F, True, "title/query alignment in [-1, 0] (0 = perfect)"),
        ("h1_relevance_gap", N, F, True, "H1/query alignment in [-1, 0] (0 = perfect)"),
        ("query_title_similarity", N, F, True, "embedding similarity query vs title"),
        ("query_h1_similarity", N, F, True, "embedding similarity query vs H1"),
        ("query_meta_similarity", N, F, True, "embedding similarity query vs meta description"),
        ("query_heading_similarity", N, F, True, "embedding similarity query vs headings"),
        ("query_term_coverage_title", N, F, True, "query terms covered by the title"),
        ("query_term_coverage_h1", N, F, True, "query terms covered by the H1"),
        ("query_term_coverage_headings", N, F, True, "query terms covered by headings"),
        ("query_term_coverage_meta", N, F, True, "query terms covered by the meta description"),
        ("query_term_coverage_body", N, F, True, "query terms covered by the body text"),
    ],
    "internal_linking": [
        ("internal_link_count", N, F, True, "internal links on the page"),
        ("unique_internal_link_count", N, F, True, "distinct internal link targets"),
        ("contextual_internal_links", N, F, True, "links inside the main content"),
        ("internal_links_to_products", N, F, True, "links to product pages"),
        ("internal_links_to_categories", N, F, True, "links to category pages"),
        ("out_degree", N, F, True, "page rank received via outbound internal links"),
        ("breadcrumb_links", N, F, True, "breadcrumb links"),
        ("orphan_page", B, F, False, "page unreachable from internal navigation"),
        ("internal_link_gap", N, K, True, "internal links minus competitor mean"),
        ("internal_links_vs_competitors", N, K, True, "internal links relative to competitors"),
    ],
    "schema_commerce": [
        ("schema_present", B, F, True, "structured data present"),
        ("schema_count", N, F, True, "number of structured data blocks"),
        ("schema_types_count", N, F, True, "distinct schema types"),
        ("has_product_schema", B, F, True, "Product schema present"),
        ("has_breadcrumb_schema", B, F, True, "BreadcrumbList schema present"),
        ("has_organization_schema", B, F, True, "Organization schema present"),
        ("has_faq_schema", B, F, True, "FAQPage schema present"),
        ("schema_completeness_score", N, F, True, "share of expected schema fields present"),
        ("commerce_completeness_score", N, F, True, "share of commerce attributes present"),
        ("trust_score", N, F, True, "trust signals score (contact, policy, author)"),
        ("price_present", B, F, True, "price rendered on the page"),
        ("availability_present", B, F, True, "stock availability rendered"),
        ("in_stock", B, F, True, "item reported in stock"),
        ("add_to_cart_present", B, F, True, "add-to-cart control present"),
        ("return_policy_present", B, F, True, "return policy communicated"),
        ("rating_present", B, F, True, "rating rendered on the page"),
        ("review_count", N, F, True, "reviews shown on the page"),
        ("contact_information_present", B, F, True, "contact information present"),
        ("privacy_policy_present", B, F, True, "privacy policy link present"),
        ("terms_conditions_present", B, F, True, "terms and conditions link present"),
        ("author_information_present", B, F, True, "author information present"),
        ("schema_gap", N, K, True, "schema completeness minus competitor mean"),
        ("commerce_gap", N, K, True, "commerce completeness minus competitor mean"),
        ("schema_score_vs_competitors", N, K, True, "schema score relative to competitors"),
        ("commerce_score_vs_competitors", N, K, True, "commerce score relative to competitors"),
    ],
    "technical_seo": [
        ("is_indexable", B, F, True, "page allows indexing"),
        ("canonical_present", B, F, True, "canonical tag present"),
        ("canonical_self_referencing", B, F, True, "canonical points at itself"),
        ("robots_index", B, F, True, "robots meta allows indexing"),
        ("robots_follow", B, F, True, "robots meta allows crawling"),
        ("https", B, F, True, "page served over HTTPS"),
        ("viewport_present", B, F, True, "responsive viewport meta present"),
        ("hreflang_present", B, F, True, "hreflang annotations present"),
        ("language_declared", B, F, True, "language declared in HTML"),
        ("charset_declared", B, F, True, "character encoding declared"),
        ("response_time_ms", N, F, False, "server response time (lower is better)"),
        ("page_size_kb", N, F, False, "HTML page size (lower is better)"),
        ("url_depth", N, F, False, "path depth of the URL"),
        ("click_depth", N, F, False, "clicks from the homepage"),
        ("redirect_count", N, F, False, "redirects before the final URL"),
        ("query_parameter_count", N, F, False, "query parameters in the URL"),
        ("url_length", N, F, False, "URL length in characters"),
    ],
    "entity_authority": [
        ("brand_mentioned_on_page", B, F, True, "entity/brand mentioned in the body"),
        ("brand_mentions_count", N, F, True, "number of brand mentions"),
        ("brand_in_h1", B, F, True, "brand token in the H1"),
        ("brand_in_url", B, F, True, "brand token in the URL"),
        ("brand_in_meta", B, F, True, "brand token in the meta description"),
        ("brand_consistency_score", N, F, True, "brand usage consistency across fields"),
        ("official_domain", B, F, True, "page hosted on the official domain"),
        ("url_has_brand", B, F, True, "URL contains the brand token"),
        ("unique_external_domains", N, F, True, "distinct external domains referenced"),
        ("external_authority_domains", N, F, True, "outbound links to authoritative domains"),
        ("citation_links", N, F, True, "citation-style external references"),
        ("brand_entity_count", N, F, True, "brand entities extracted from the page"),
    ],
    "query_context": [
        ("query_word_count", N, F, True, "words in the analysed query"),
        ("query_character_count", N, F, True, "characters in the analysed query"),
        ("query_brand_mentioned", B, F, True, "query mentions a brand"),
        ("query_intent", C, F, True, "classified query intent"),
        ("query_category", C, F, True, "classified query category"),
    ],
}

# Columns carrying outcome roles; excluded from the feature side of tests.
OUTCOME_FEATURES: list[str] = ["serp_position", "svtp_score", "page_trend", "rank_group"]

ACTION_FAMILIES = [
    "content_enrichment",
    "metadata_optimization",
    "internal_linking",
    "schema_commerce",
    "technical_seo",
    "entity_authority",
]


def build_feature_registry() -> FeatureRegistry:
    """Declare the domain feature set once for every engine to read."""
    registry = FeatureRegistry()
    for family, declarations in _DECLARATIONS.items():
        for name, kind, role, higher_is_better, description in declarations:
            registry.register(
                FeatureSpec(
                    name=name,
                    family=family,
                    kind=kind,
                    role=role,
                    higher_is_better=higher_is_better,
                    expected_direction="positive" if higher_is_better else "negative",
                    description=description,
                )
            )
    for name, kind, description in [
        ("serp_position", N, "observed organic rank (1 = best)"),
        ("svtp_score", N, "Search Visibility Trend Proxy in [0, 1]"),
        ("page_trend", N, "binary label derived from the SVTP ranking"),
        ("rank_group", C, "SERP band 1-3 / 4-6 / 7-10"),
    ]:
        registry.register(
            FeatureSpec(
                name=name,
                family="outcome",
                kind=kind,
                role=OUT,
                higher_is_better=None,
                description=description,
            )
        )
    return registry


FEATURE_REGISTRY = build_feature_registry()
