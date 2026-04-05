---
paths:
  - "klai-knowledge-ingest/**"
  - "klai-connector/**"
---
# Knowledge Domain Patterns

## crawl4ai DOM selectors
- Never use `[class*="sidebar"]` or other substring CSS selectors in JS removal scripts.
- Use only semantic element selectors (`nav`, `header`, `aside`) and ARIA roles.

## notion_client v2 — databases.query() removed (MED)

`notion_client` v2 removed `databases.query()`. The only available search API is
`client.search()`, which returns all pages the integration can access — it cannot
be filtered by `database_ids` at the client level.

The `database_ids` config field is stored and surfaced in the UI (SPEC-KB-019) but
does not filter API results. Future filtering must be applied post-fetch (compare
`parent.database_id` against the stored list), not via an SDK call.

**Rule:** Never assume `notion_client` has a database-scoped query method. Filter by `database_id` in Python after fetching all search results.
- Spot-check `raw_words` on a known-good page after any crawl config change.

## crawl4ai usage
- **Both klai-knowledge-ingest and klai-connector**: HTTP REST API client to `http://crawl4ai:11235` — `POST /crawl` (sync), processes markdown results.
- Crawl4AI runs as a shared Docker container with Playwright; neither service has a local browser install.
- Connector uses `POST /crawl` with batches of up to 100 URLs (sitemap supplement strategy).
- No other services use crawl4ai. Firecrawl is a separate service used by the chat application only.

## Embedding pipeline (knowledge-ingest)
1. Chunking: 1500 chars, 200-char overlap
2. Dense embeddings via TEI (gpu-01, port 7997, BAAI/bge-m3, batch size 32, timeout 120s)
3. Sparse embeddings via bge-m3-sparse (gpu-01, port 8001)
4. Store in Qdrant: hybrid dense + sparse + metadata
5. Retrieval: query → dense + BM25 sparse → rerank top-20 via Infinity (gpu-01, port 7998) → top-10 to LLM
