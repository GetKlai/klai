"""fix/timeout-race-vs-batch-process — the bulk-crawl httpx timeout must
keep margin over crawl4ai's own server-side ``timeouts.batch_process``.

Regression coverage for the 2026-08-18 timeout-race fix: the running
crawl4ai REST server is configured with ``timeouts.batch_process: 300.0``
(confirmed against the running server config, 2026-08-18). Our own bulk
``/crawl`` httpx timeout (``settings.crawl_bulk_base_timeout_seconds``) was
ALSO 300.0 — an exact match, with zero margin. On a borderline-slow chunk it
is unpredictable whether our own httpx client or crawl4ai's server-side
batch_process guard fires first: sometimes we get a clean (if late) server
response, sometimes our own client cuts the connection first and the real
server-side outcome (including a diagnosable error body) is lost. Every
other timeout in this codebase's crawl pacing/recovery chain keeps
deliberate margin over the value it bounds (see
``crawl_sequential_recovery_timeout_seconds`` vs the 180s crawl4ai
429-backoff ceiling, and the 90s seed timeout vs the 30s page_timeout) —
this is the only exception, which is exactly the invariant this file locks
in.
"""

from __future__ import annotations

from knowledge_ingest.config import settings

# The crawl4ai REST server's own `timeouts.batch_process` setting. Not
# importable from crawl4ai (it's server-side deploy config, not part of the
# client SDK) — pinned here as the known value so this test fails loudly if
# either side of the relationship changes without the other being revisited.
KNOWN_CRAWL4AI_SERVER_BATCH_PROCESS_TIMEOUT_SECONDS = 300.0


def test_bulk_timeout_keeps_margin_over_server_batch_process_timeout() -> None:
    """Our client-side bulk timeout must be strictly greater than crawl4ai's
    own server-side batch_process timeout — otherwise the two race with no
    margin, and a borderline-slow chunk can hit either guard unpredictably.
    """
    assert (
        settings.crawl_bulk_base_timeout_seconds
        > KNOWN_CRAWL4AI_SERVER_BATCH_PROCESS_TIMEOUT_SECONDS
    ), (
        f"crawl_bulk_base_timeout_seconds={settings.crawl_bulk_base_timeout_seconds}s "
        f"must exceed crawl4ai's own batch_process timeout="
        f"{KNOWN_CRAWL4AI_SERVER_BATCH_PROCESS_TIMEOUT_SECONDS}s — an exact match "
        "is a race, not a vangnet."
    )
