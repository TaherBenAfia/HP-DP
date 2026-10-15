"""Action families for the sports SEO domain.

Six transparent families, each defined as:

* an explicit feature list (from ``features.FEATURE_REGISTRY``);
* an explicit target rule per feature (binary presence, acceptable range, or
  empirical distribution);
* an explicit recommendation sentence for the strongest deficit.

Family score = weighted mean of feature deficits. Statistical evidence only
changes feature weights (see ``src/analytics/action_engine.py``); it never
adds an unexplained component.
"""

from __future__ import annotations

from src.analytics.action_engine import ActionFamily, DeficitRule

_RANGE_TITLE = DeficitRule(kind="range", low=30.0, high=60.0, note="30-60 characters")
_RANGE_META = DeficitRule(kind="range", low=50.0, high=160.0, note="50-160 characters")
_ABSENT = DeficitRule(kind="binary", desired=False, note="defect when present")

FAMILIES: list[ActionFamily] = [
    ActionFamily(
        name="content_enrichment",
        description="Depth, coverage and semantic quality of the page content",
        features=[
            "word_count",
            "content_depth",
            "unique_word_count",
            "lexical_diversity",
            "topic_count",
            "topic_coverage_score",
            "topical_authority_score",
            "semantic_coherence_score",
            "heading_count",
            "h1_word_count",
            "alt_text_coverage",
            "images_without_alt",
            "named_entity_count",
            "sports_entity_count",
            "content_relevance_gap",
            "content_gap",
            "content_depth_vs_competitors",
        ],
        weights={
            "word_count": 1.0,
            "content_depth": 1.2,
            "topic_coverage_score": 1.5,
            "topical_authority_score": 1.2,
            "semantic_coherence_score": 1.0,
            "content_relevance_gap": 1.5,
            "alt_text_coverage": 0.8,
            "images_without_alt": 0.8,
            "content_gap": 1.0,
            "content_depth_vs_competitors": 1.0,
        },
        rules={
            "images_without_alt": _ABSENT,
            "alt_text_coverage": DeficitRule(kind="range", low=0.8, high=1.0),
        },
        recommendations={
            "word_count": "Expand the page with substantial, query-relevant copy and examples.",
            "content_depth": "Deepen the page: add sections, subtopics and concrete detail.",
            "topic_coverage_score": "Cover the query's missing subtopics; add headings for each.",
            "content_relevance_gap": "Rewrite the introduction so the query topic appears early and often.",
            "alt_text_coverage": "Write descriptive alt text for every image.",
            "images_without_alt": "Add alt attributes to images missing them.",
            "content_gap": "Publish more content than the competitors ranking for this query.",
            "semantic_coherence_score": "Restructure the copy so paragraphs stay on one topic.",
        },
        summary=(
            "Enrich the content: cover the missing subtopics, deepen the existing "
            "sections and make sure every query term appears naturally in the copy."
        ),
    ),
    ActionFamily(
        name="metadata_optimization",
        description="Title, meta description, headings and query-term placement",
        features=[
            "title_present",
            "title_length",
            "meta_description_present",
            "meta_description_length",
            "keyword_in_title",
            "keyword_in_meta_description",
            "cta_in_title",
            "cta_in_meta_description",
            "brand_in_title",
            "keyword_in_h1",
            "keyword_in_headings",
            "title_relevance_gap",
            "h1_relevance_gap",
            "query_title_similarity",
            "query_h1_similarity",
            "query_meta_similarity",
            "query_heading_similarity",
            "query_term_coverage_title",
            "query_term_coverage_h1",
            "query_term_coverage_headings",
            "query_term_coverage_meta",
        ],
        weights={
            "title_present": 2.0,
            "title_length": 1.5,
            "meta_description_present": 1.5,
            "meta_description_length": 1.0,
            "keyword_in_title": 2.0,
            "keyword_in_meta_description": 1.2,
            "keyword_in_h1": 1.5,
            "title_relevance_gap": 1.5,
            "query_title_similarity": 1.5,
            "query_term_coverage_title": 1.5,
        },
        rules={
            "title_length": _RANGE_TITLE,
            "meta_description_length": _RANGE_META,
        },
        recommendations={
            "title_present": "Add a unique HTML title that states the page's topic.",
            "title_length": "Rewrite the title to 30-60 characters including the query.",
            "meta_description_present": "Add a meta description summarising the page.",
            "meta_description_length": "Keep the meta description between 50 and 160 characters.",
            "keyword_in_title": "Put the query keyword in the title, near the beginning.",
            "keyword_in_meta_description": "Include the query keyword in the meta description.",
            "keyword_in_h1": "Put the query keyword into the H1 heading.",
            "title_relevance_gap": "Align the title wording with the query wording.",
            "h1_relevance_gap": "Align the H1 wording with the query wording.",
            "query_title_similarity": "Make the title express the same meaning as the query.",
            "query_term_coverage_title": "Include the missing query terms in the title.",
        },
        summary=(
            "Optimise the metadata: unique 30-60 character title and matching H1 "
            "carrying the query keyword, plus a 50-160 character meta description."
        ),
    ),
    ActionFamily(
        name="internal_linking",
        description="Internal link volume, context, navigation and orphan status",
        features=[
            "internal_link_count",
            "unique_internal_link_count",
            "contextual_internal_links",
            "internal_links_to_products",
            "internal_links_to_categories",
            "out_degree",
            "breadcrumb_links",
            "orphan_page",
            "internal_link_gap",
            "internal_links_vs_competitors",
        ],
        weights={
            "internal_link_count": 1.2,
            "contextual_internal_links": 1.5,
            "out_degree": 1.0,
            "orphan_page": 2.0,
            "internal_link_gap": 1.2,
        },
        rules={"orphan_page": DeficitRule(kind="binary", desired=False)},
        recommendations={
            "orphan_page": "Link to this page from at least one indexable navigation or body link.",
            "internal_link_count": "Add contextual internal links from related pages and sections.",
            "contextual_internal_links": "Add links inside the main content, not only in menus.",
            "internal_link_gap": "Raise internal link count above the competitor mean for this query.",
            "internal_links_to_products": "Link the supporting pages to the relevant product pages.",
        },
        summary=(
            "Strengthen internal linking: remove orphan status, add contextual links "
            "from related pages and match the competitors' internal link volume."
        ),
    ),
    ActionFamily(
        name="schema_commerce",
        description="Structured data, commerce completeness and trust signals",
        features=[
            "schema_present",
            "schema_count",
            "schema_types_count",
            "has_product_schema",
            "has_breadcrumb_schema",
            "has_organization_schema",
            "has_faq_schema",
            "schema_completeness_score",
            "commerce_completeness_score",
            "trust_score",
            "price_present",
            "availability_present",
            "in_stock",
            "add_to_cart_present",
            "return_policy_present",
            "rating_present",
            "review_count",
            "contact_information_present",
            "privacy_policy_present",
            "terms_conditions_present",
            "author_information_present",
            "schema_gap",
            "commerce_gap",
            "schema_score_vs_competitors",
            "commerce_score_vs_competitors",
        ],
        weights={
            "schema_present": 2.0,
            "schema_completeness_score": 1.5,
            "has_product_schema": 1.5,
            "commerce_completeness_score": 1.5,
            "trust_score": 1.2,
            "schema_gap": 1.0,
            "price_present": 1.2,
            "availability_present": 1.0,
            "privacy_policy_present": 1.0,
            "return_policy_present": 1.0,
        },
        rules={
            "schema_present": DeficitRule(kind="binary", desired=True),
            "privacy_policy_present": DeficitRule(kind="binary", desired=True),
            "terms_conditions_present": DeficitRule(kind="binary", desired=True),
        },
        recommendations={
            "schema_present": "Add JSON-LD structured data (Organization, WebPage, Breadcrumb).",
            "schema_completeness_score": "Fill in the missing required and recommended schema fields.",
            "has_product_schema": "Add Product schema with offers for this product page.",
            "commerce_completeness_score": "Expose price, stock, SKU, shipping and return details.",
            "trust_score": "Add contact details, policy links and author information.",
            "price_present": "Render the price visibly on the page.",
            "privacy_policy_present": "Link the privacy policy from this page.",
            "return_policy_present": "State the return policy on the page.",
            "rating_present": "Render ratings/reviews with Review markup.",
        },
        summary=(
            "Complete the structured data and commerce markup: Product/Breadcrumb "
            "schema, price and stock visibility, plus trust and policy signals."
        ),
    ),
    ActionFamily(
        name="technical_seo",
        description="Indexability, canonicalisation, performance and URL structure",
        features=[
            "is_indexable",
            "canonical_present",
            "canonical_self_referencing",
            "robots_index",
            "robots_follow",
            "https",
            "viewport_present",
            "hreflang_present",
            "language_declared",
            "charset_declared",
            "response_time_ms",
            "page_size_kb",
            "url_depth",
            "click_depth",
            "redirect_count",
            "query_parameter_count",
            "url_length",
        ],
        weights={
            "is_indexable": 2.5,
            "robots_index": 2.5,
            "canonical_present": 1.5,
            "canonical_self_referencing": 1.0,
            "https": 2.0,
            "response_time_ms": 1.5,
            "click_depth": 1.2,
            "redirect_count": 1.0,
            "viewport_present": 1.2,
        },
        rules={
            "response_time_ms": DeficitRule(kind="range", low=0.0, high=500.0),
            "page_size_kb": DeficitRule(kind="range", low=0.0, high=150.0),
            "url_depth": DeficitRule(kind="range", low=0.0, high=4.0),
            "click_depth": DeficitRule(kind="range", low=0.0, high=3.0),
            "redirect_count": DeficitRule(kind="range", low=0.0, high=0.0),
            "query_parameter_count": DeficitRule(kind="range", low=0.0, high=0.0),
            "url_length": DeficitRule(kind="range", low=0.0, high=100.0),
        },
        recommendations={
            "is_indexable": "Remove the noindex directive so search engines may index the page.",
            "robots_index": "Fix the robots meta to allow indexing.",
            "canonical_present": "Add a canonical tag pointing at the preferred URL.",
            "canonical_self_referencing": "Point the canonical tag at this URL.",
            "https": "Serve the page over HTTPS and redirect the HTTP version.",
            "response_time_ms": "Reduce server response time below 500 ms (cache, compression).",
            "page_size_kb": "Reduce the HTML payload below 150 KB.",
            "url_depth": "Flatten the URL to at most four path segments.",
            "click_depth": "Link the page from the homepage or a top-level hub.",
            "redirect_count": "Remove redirects; link directly to the final URL.",
        },
        summary=(
            "Fix technical hygiene: indexable + self-referencing canonical, HTTPS, "
            "fast response, shallow URLs and no redirect chains."
        ),
    ),
    ActionFamily(
        name="entity_authority",
        description="Entity/brand signals and external authority references",
        features=[
            "brand_mentioned_on_page",
            "brand_mentions_count",
            "brand_in_h1",
            "brand_in_url",
            "brand_in_meta",
            "brand_consistency_score",
            "official_domain",
            "url_has_brand",
            "unique_external_domains",
            "external_authority_domains",
            "citation_links",
            "brand_entity_count",
        ],
        weights={
            "brand_mentioned_on_page": 1.5,
            "brand_consistency_score": 1.5,
            "official_domain": 1.5,
            "brand_in_h1": 1.0,
            "external_authority_domains": 1.0,
            "citation_links": 0.8,
        },
        rules={},
        recommendations={
            "brand_mentioned_on_page": "Mention the entity/brand explicitly in the body copy.",
            "brand_in_h1": "Include the brand token in the H1.",
            "brand_consistency_score": "Use one brand spelling across title, H1, meta and URL.",
            "official_domain": "Publish the page on the official domain or link to it.",
            "brand_in_url": "Include the brand token in the URL slug.",
            "external_authority_domains": "Cite authoritative external sources for key claims.",
        },
        summary=(
            "Consolidate entity signals: consistent brand naming across title/H1/meta/URL "
            "and references to authoritative external sources."
        ),
    ),
]
