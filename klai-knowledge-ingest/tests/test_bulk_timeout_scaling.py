"""fix/bulk-timeout-scales-with-pacing — the bulk-crawl httpx timeout must
scale with the pacing WE impose via ``rate_limit``.

Regression coverage for the 2026-08-17/2026-08-18 intermedia.com incident:
since PR #1034, ``rate_limit`` is translated into crawl4ai's own pacing
controls (``semaphore_count`` + ``mean_delay`` — see
``crawl4ai_client.build_crawl_config``). A single-domain crawl is paced by
``mean_delay`` per URL (crawl4ai's ``RateLimiter`` keeps per-domain state and
serialises starts globally, regardless of ``semaphore_count``), so a chunk's
minimum duration is >= ``len(chunk_urls) * mean_delay``. The httpx timeout
around the bulk request stayed fixed at 300.0s regardless — at
``rate_limit=0.25`` (``mean_delay=4.0s``) a 100-URL chunk needs >= 400s of
pure self-imposed pacing alone, so the request timed out on its own
deliberate delay before a real failure could even be observed. Production
evidence (17-08 22:00, intermedia.com, rate_limit=0.5): 146 timeout outcomes
against 254 real crawl4ai-side 429s in the container log — the fixed timeout
cut the request off before crawl4ai's own rate-limit signal came back.

The invariant this file locks in: **we must never time out on vertraging die
we zelf hebben ingesteld.**
"""

from __future__ import annotations

import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.config import settings


class TestBulkCrawlTimeoutScalesWithPacing:
    @pytest.mark.parametrize("rate_limit", [2.0, 1.0, 0.5, 0.25])
    def test_timeout_exceeds_minimum_chunk_pacing_duration(self, rate_limit: float) -> None:
        """For every supported rate_limit, the computed bulk timeout must be
        strictly greater than the minimum time a full 100-URL chunk needs
        just to pace itself (``chunk_size * mean_delay``) — otherwise the
        client times out on its own deliberate delay before crawl4ai's
        slowest page even starts fetching. This is the invariant that was
        broken in production; it must hold for every rate_limit we support.
        """
        chunk_size = 100
        mean_delay = 1.0 / rate_limit
        crawler_config = crawl4ai_client.build_crawl_config(None, rate_limit=rate_limit)

        timeout = crawl4ai_client._bulk_crawl_timeout_for_chunk(chunk_size, crawler_config)

        minimum_pacing_duration = chunk_size * mean_delay
        assert timeout > minimum_pacing_duration, (
            f"rate_limit={rate_limit}: computed timeout={timeout}s must exceed "
            f"the chunk's own minimum pacing duration={minimum_pacing_duration}s "
            "— otherwise we time out on vertraging die we zelf hebben ingesteld."
        )

    def test_quarter_rps_100_url_chunk_needs_well_over_400_seconds(self) -> None:
        """Concrete regression for the production incident: rate_limit=0.25
        (mean_delay=4.0s) on a 100-URL chunk needs >= 400s of pure pacing
        before a single failure signal could even occur. Today (before the
        fix) the bulk timeout is a fixed 300.0s — this assertion fails
        against that fixed value."""
        crawler_config = crawl4ai_client.build_crawl_config(None, rate_limit=0.25)

        timeout = crawl4ai_client._bulk_crawl_timeout_for_chunk(100, crawler_config)

        assert timeout > 400.0

    def test_default_timeout_unchanged_without_rate_limit(self) -> None:
        """No rate_limit means no mean_delay key in crawler_config — no
        client-imposed pacing exists, so the timeout stays exactly the
        historical 300.0s default. Every caller that never sets rate_limit
        must see byte-for-byte unchanged behaviour."""
        crawler_config = crawl4ai_client.build_crawl_config(None)  # no rate_limit

        timeout = crawl4ai_client._bulk_crawl_timeout_for_chunk(100, crawler_config)

        assert timeout == 300.0

    def test_uses_configured_base_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The base component is sourced from settings (in line with the
        other crawl_* settings in config.py), not a hardcoded module
        constant, so operators can tune it without a code change."""
        monkeypatch.setattr(settings, "crawl_bulk_base_timeout_seconds", 120.0)
        crawler_config = crawl4ai_client.build_crawl_config(None)  # no rate_limit

        timeout = crawl4ai_client._bulk_crawl_timeout_for_chunk(100, crawler_config)

        assert timeout == 120.0

    def test_formula_matches_base_plus_pacing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Documents the exact formula: base + chunk_size * mean_delay. At
        rate_limit=0.25 that is 300 + 100*4.0 = 700.0 with the default base."""
        crawler_config = crawl4ai_client.build_crawl_config(None, rate_limit=0.25)

        timeout = crawl4ai_client._bulk_crawl_timeout_for_chunk(100, crawler_config)

        assert timeout == pytest.approx(700.0)
