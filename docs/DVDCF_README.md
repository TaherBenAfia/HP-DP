# DVDCF — Digital Visibility Data Collection Framework

Research-grade measurement of sports-brand web visibility (French market). The
framework crawls URLs discovered in Google SERP JSON exports, extracts page
structure, engineers ~180 features per page (commerce, schema.org, sports
mentions, NLP, graph metrics), and stores the result in a Parquet data lake plus
SQLite for analysis.

## Architecture

Stage-driven DAG orchestrated by `src/pipeline.py`:

```
ingestion → dedup → classification → frontier → crawl → extraction → NLP
→ feature engineering → graph → storage
```

- `src/modules/ingestion/` — validate + normalize SERP JSON files
- `src/modules/discovery/` — dedup (Bloom filter + SimHash), URL classification, robots/sitemap enrichment
- `src/modules/scheduler/` — priority frontier + domain politeness rate limiter
- `src/modules/crawler/` — aiohttp fetcher (retries/backoff), robots compliance, content normalization
- `src/modules/extraction/` — DOM extraction, schema.org parsing
- `src/modules/nlp/` — language detection, intent, sentiment, embeddings (TF-IDF fallback)
- `src/modules/features/` — 10 extractors + 185-feature registry (`config/feature_schema.yaml`)
- `src/modules/graph/` — page graph with PageRank/betweenness/clustering (no networkx dependency)
- `src/modules/storage/` — SQLite feature/graph store, checkpoints, Parquet data lake

Design rules: every module is import-independent (the pipeline composes them),
optional heavy dependencies (playwright, spacy, sentence-transformers, networkx,
duckdb) are lazy-imported with fallbacks, and politeness is enforced at 6
req/min/domain with crawl depth 5.

## Setup

Python >= 3.11.

```bash
pip install -e .
pip install -e ".[dev]"      # pytest, mypy, ruff
pip install -e ".[all]"      # optional: playwright, spacy, networkx, duckdb
```

Not required at runtime: `Brotli` (the fetcher only advertises `br` when it is
installed), `langdetect`, `sentence-transformers` (TF-IDF fallback used instead).

## Input data

Query-keyed JSON documents in `data/input_jsons/`:

```json
{
  "sports shoes decathlon": {
    "searchParameters": {"q": "...", "type": "search", "engine": "google"},
    "organic": [{"title": "...", "link": "...", "snippet": "...", "position": 1}],
    "peopleAlsoAsk": [{"question": "...", "snippet": "..."}],
    "relatedSearches": [{"query": "..."}],
    "credits": 5
  }
}
```

## Usage

```bash
# Discovery-only (no network): ingest, dedup, plan, PAA, exports
python -m src.pipeline --dry-run --label test1

# Real crawl (one page, full end-to-end)
python -m src.pipeline --crawl --limit 1 --label smoke

# Real crawl with robots.txt/sitemap enrichment
python -m src.pipeline --crawl --enrich --label run1 --limit 500

# Resume an interrupted crawl from checkpoint
python scripts/resume_crawl.py --label run1

# Export stored vectors
python scripts/export_dataset.py --output data/exported_dataset.csv
```

### Outputs

| Path | Content |
| --- | --- |
| `data/paa_<label>.csv` | People-Also-Ask blocks (question/snippet per query) |
| `data/urls_<label>.csv` | Planned/fetched URLs with page type, priority, depth |
| `data_lake/<source>.json.parquet` | Feature vectors partitioned by source file + `manifest.json` |
| `data/dvdcf.db` | SQLite feature + graph store |
| `data/checkpoints/` | Resumable crawl state |
| `logs/dvdcf.log` | Rotating structured logs |

## Quality gates

```bash
python -m pytest                     # 101 tests
ruff check . && ruff format --check .
python -m mypy src                   # strict mode, 0 errors
```

## Configuration

Everything is driven from `config/default.yaml` (no constants in source):
ingestion dirs, politeness budgets, crawl depth, feature schema location,
storage paths. Auxiliary documents: `config/feature_schema.yaml`,
`config/sports_taxonomy.yaml`, gazetteers in `config/gazetteers/`.

## Documentation

- `architecture.md` — full pipeline design (ingestion → NLP → graph → storage)
- `agent.md` — working notes and conventions
