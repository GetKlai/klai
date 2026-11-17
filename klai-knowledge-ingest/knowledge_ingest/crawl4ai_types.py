"""Shared data contracts for the Crawl4AI client and crawl orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from knowledge_ingest.reason_codes import FetchReasonCode

FetchOutcome = dict[str, Any]


class CrawlCheckpoint(Protocol):
    """Durable seam used by ``crawl_site`` at committed batch boundaries."""

    async def load(self) -> dict[str, Any] | None: ...

    async def save(self, snapshot: dict[str, Any]) -> None: ...

    async def ensure_active(self) -> None: ...


@dataclass
class CrawlMemoryState:
    """Handoff between a connector's crawl memory and ``crawl_site``.

    ``remembered`` is what the memory held before this crawl: rows with
    ``canonical_url`` (the memory key), ``url``, ``depth`` (the depth a crawl
    really found it at), ``first_seen_at`` and ``last_visited_at`` (epoch
    seconds; ``last_visited_at`` is ``None`` for an address never tried).
    ``crawl_site`` reads it and fills the rest when it completes; it never
    writes the memory itself, so the adapter can record a run only once its
    pages were accepted.

    ``rows`` is every in-scope address the crawl's frontier held, with
    ``visited`` (actually sent this crawl); ``forget`` holds memory keys to
    drop in a rotation crawl (an address only the memory knew that answered
    404/410, an out-of-scope or re-keyed row).
    """

    remembered: list[dict[str, Any]]
    rows: list[dict[str, Any]] = field(default_factory=list)
    forget: list[str] = field(default_factory=list)

    @property
    def whole_site(self) -> bool:
        """True when this crawl runs as a whole-site crawl, exactly as without memory.

        That is the case when the memory is empty or the most recent recorded
        crawl tried every remembered address (one recording is one
        transaction, so they all carry the same time). Otherwise some address
        still waits from an earlier crawl and this crawl rotates.
        """
        tried = {row["last_visited_at"] for row in self.remembered}
        return not self.remembered or (len(tried) == 1 and None not in tried)


@dataclass
class CrawlResult:
    """Normalised result from a single-page crawl."""

    url: str
    fit_markdown: str
    raw_markdown: str
    html: str
    word_count: int
    success: bool
    requested_url: str | None = None
    links: dict[str, list[dict]] = field(default_factory=dict)
    media: dict[str, list[dict]] = field(default_factory=dict)
    error_message: str | None = None
    metadata: dict[str, Any] | None = None
    response_headers: dict[str, str] | None = None
    status_code: int | None = None
    error_type: str | None = None
    raw_error_text: str | None = None
    # crawl4ai's HTML after excluded_tags and excluded_selector: what the
    # login indicator is matched against, so a user menu in a stripped
    # header never reads as a login wall (the page-side check used to run
    # after the same chrome removal).
    cleaned_html: str = ""


@dataclass
class CrawlRateLimitState:
    """Mutable handoff of the rate actually in effect when ``crawl_site`` ends."""

    current_rate_limit: float | None


DiscoverySourceKind = Literal["start", "sitemap", "page_link", "known"]
DiscoveryStatus = Literal["queued", "fetched", "omitted"]


@dataclass
class DiscoveredUrl:
    """One URL in Klai's deterministic crawl frontier ledger."""

    url: str
    canonical_url: str
    depth: int
    discovered_from: str | None
    source_kind: DiscoverySourceKind
    priority: int
    order: int
    status: DiscoveryStatus = "queued"
    reason_code: str | None = None
    # From the connector's crawl memory; not stored in the checkpoint
    # frontier, a resumed crawl reads it from the memory again.
    last_visited_at: float | None = None
    first_seen_at: float | None = None


@dataclass
class LinkedPageSample:
    """Outcome of sampling pages linked from an already-crawled page."""

    pages_crawled: int
    pages_usable: int


@dataclass
class ChunkedFetchResult:
    """Accumulated outcomes from one chunked Crawl4AI bulk fetch."""

    raw_results: list[dict[str, Any]] = field(default_factory=list)
    failed: dict[str, BaseException] = field(default_factory=dict)
    not_attempted: list[str] = field(default_factory=list)
    stopped_early: bool = False
    stop_trigger_reason_code: str | None = None
    circuit_breaker_triggered: bool = False
    circuit_breaker_slowdown_triggered: bool = False
    not_attempted_reason_code: str = FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value
    cancelled: bool = False
