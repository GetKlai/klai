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


@dataclass
class CrawlRateLimitState:
    """Mutable handoff of the rate actually in effect when ``crawl_site`` ends."""

    current_rate_limit: float | None


DiscoverySourceKind = Literal["start", "sitemap", "page_link"]
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
