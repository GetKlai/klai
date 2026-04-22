## Task Decomposition
SPEC: SPEC-CRAWLER-005

| Task ID | Description | Requirement | Dependencies | Planned Files | Status |
|---------|-------------|-------------|--------------|---------------|--------|
| T-001 | Write failing `test_build_link_graph.py` (RED) | REQ-01.2, REQ-01.4 | - | klai-knowledge-ingest/tests/test_build_link_graph.py | pending |
| T-002 | Write failing `test_crawler_link_fields_complete.py` (RED) | REQ-01.1, REQ-02.1-02.4 | T-001 | klai-knowledge-ingest/tests/test_crawler_link_fields_complete.py | pending |
| T-003 | Add `_build_link_graph` helper to crawler.py (GREEN) | REQ-01.2 | T-001 | klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py | pending |
| T-004 | Refactor `run_crawl_job` to two-phase (GREEN) | REQ-01.1 | T-003 | klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py | pending |
| T-005 | Remove page_links upsert from `_ingest_crawl_result` (GREEN) | REQ-01.3 | T-003 | klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py | pending |
| T-006 | Remove post-crawl update_link_counts block (GREEN) | REQ-05.1 | T-004 | klai-knowledge-ingest/knowledge_ingest/adapters/crawler.py | pending |
| T-007 | Deprecation docstrings on legacy functions (REFACTOR) | REQ-05.2 | T-006 | link_graph.py, qdrant_store.py | pending |
| T-008 | Fix 3 pre-existing test_crawl_link_fields.py failures | REQ-06.1 | - | klai-knowledge-ingest/knowledge_ingest/routes/crawl.py | pending |
| T-009 | chunk_type diagnose + structured warning log | REQ-03.2 | - | klai-knowledge-ingest/knowledge_ingest/enrichment.py | pending |
| T-010 | chunk_type fix (retry or fallback) | REQ-03.1 | T-009 | klai-knowledge-ingest/knowledge_ingest/enrichment.py | pending |
| T-011 | Write test_chunk_type_crawl.py | REQ-03.1, REQ-03.2 | T-010 | klai-knowledge-ingest/tests/test_chunk_type_crawl.py | pending |
| T-012 | Create payload_list helper + unit tests | REQ-04.1 | - | klai-retrieval-api/app/util/payload.py, tests/test_payload_util.py | pending |
| T-013 | Sweep retrieval-api call sites to payload_list | REQ-04.2 | T-012 | klai-retrieval-api/app/** | pending |
| T-014 | Integration test docker-compose (env-gated) | REQ-07.1 | T-006 | klai-knowledge-ingest/tests/integration/test_crawl_sync_end_to_end.py | pending |
| T-015 | Playwright E2E on Voys support | REQ-08.1, REQ-08.3 | T-006, T-010, T-013 | - | pending |
| T-016 | Update knowledge-ingest-flow.md Part 2 (two-phase diagram) | REQ-04.3 | - | docs/architecture/knowledge-ingest-flow.md | pending |
| T-017 | Add pitfall entry graph-first content-second | - | - | .claude/rules/klai/projects/knowledge.md | pending |
| T-018 | Set SPEC frontmatter status=completed | - | T-001..T-017 | .moai/specs/SPEC-CRAWLER-005/spec.md | pending |
