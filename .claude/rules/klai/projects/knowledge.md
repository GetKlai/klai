---
paths:
  - "klai-knowledge-ingest/**"
  - "klai-connector/**"
---
# Knowledge Domain Patterns

## crawl4ai DOM selectors
- Never use `[class*="sidebar"]` or other substring CSS selectors in JS removal scripts.
- Use only semantic element selectors (`nav`, `header`, `aside`) and ARIA roles.
- Spot-check `raw_words` on a known-good page after any crawl config change.

## crawl4ai usage
- **klai-knowledge-ingest**: direct Python library (`crawl4ai>=0.4,<1`) — full control over crawl config, JS execution, content filtering.
- **klai-connector**: HTTP REST API client to `http://crawl4ai:11235` — submits async jobs, polls completion, processes markdown results.
- No other services use crawl4ai.

## Embedding pipeline (knowledge-ingest)
1. Chunking: 1500 chars, 200-char overlap
2. Dense embeddings via TEI (gpu-01, port 7997, BAAI/bge-m3, batch size 32, timeout 120s)
3. Sparse embeddings via bge-m3-sparse (gpu-01, port 8001)
4. Store in Qdrant: hybrid dense + sparse + metadata
5. Retrieval: query → dense + BM25 sparse → rerank top-20 via Infinity (gpu-01, port 7998) → top-10 to LLM
