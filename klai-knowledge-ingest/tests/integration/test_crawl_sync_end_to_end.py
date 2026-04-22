"""End-to-end crawl pipeline verification (SPEC-CRAWLER-005 Fase 5, REQ-07.1).

Exercises ``run_crawl_job`` through the two-phase pipeline against a
stubbed crawl4ai, with a synthetic 4-page cross-linked site, verifying
that every Qdrant chunk ends up with the correct link-graph payload
fields at first write — no post-crawl ``set_payload`` band-aid.

## Harness

This test is env-gated with ``RUN_INTEGRATION=1`` because it requires
Postgres + Qdrant connectivity. When the env flag is NOT set, the test
auto-skips so the default ``uv run pytest`` stays fast.

### Lightweight mode (RUN_INTEGRATION unset)

Skipped. Scope documented for future fill-in. The Qdrant payload
contract this test would verify is already covered by
``tests/test_crawler_link_fields_complete.py`` (5-page cross-linked
pipeline test with mocks), so end-to-end behavioural coverage is not
zero — only the infra-in-the-loop reproduction is deferred.

### Full mode (RUN_INTEGRATION=1)

Brings up postgres + qdrant via ``docker compose -f docker-compose.test.yml``,
points knowledge-ingest at a stub crawl4ai FastAPI app serving 4
pre-canned HTML pages (A -> B, A -> C, B -> C, C -> D, D -> A with
inline ``<img>`` tags), POSTs to ``/ingest/v1/crawl/sync``, polls the
status endpoint until ``completed``, then asserts:

- ``knowledge.crawled_pages`` has 4 rows
- ``knowledge.page_links`` >= 5 rows
- Qdrant chunks (filter org+kb+source_type=crawl) is 4-20
- Every chunk has ``source_type=crawl``, ``source_label`` set,
  ``anchor_texts`` non-empty for pages with inbound links,
  ``links_to`` non-empty (<=20) for pages with outbound links,
  integer ``incoming_link_count``
- ``image_urls`` is set when fixtures had ``<img>`` tags

Expected runtime: < 60 s.

Acceptance: SPEC-CRAWLER-005 AC-07.1.
"""
from __future__ import annotations

import os

import pytest

_REQUIRES_INTEGRATION = os.getenv("RUN_INTEGRATION") != "1"


@pytest.mark.skipif(
    _REQUIRES_INTEGRATION,
    reason=(
        "Integration test requires docker-compose postgres + qdrant + stub "
        "crawl4ai. Run with RUN_INTEGRATION=1. Full stub harness is deferred "
        "(see module docstring); in-process end-to-end behaviour is covered "
        "by test_crawler_link_fields_complete.py."
    ),
)
@pytest.mark.asyncio
async def test_crawl_sync_end_to_end_full_stack() -> None:
    """Full docker-compose integration crawl — AC-07.1.

    This is a placeholder. The stub crawl4ai fixture + docker-compose
    orchestration is scoped for a follow-up PR. The assertions below are
    the contract.
    """
    pytest.skip(
        "RUN_INTEGRATION=1 is set but the stub-crawl4ai harness has not "
        "been shipped yet. Track as SPEC-CRAWLER-005 Fase 5 follow-up. "
        "Fase 6 (live Playwright on Voys `support`) provides real prod "
        "verification in the meantime."
    )
