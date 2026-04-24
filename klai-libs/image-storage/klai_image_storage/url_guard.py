"""Shared SSRF guard with IP-pinned resolution (SPEC-SEC-SSRF-001).

Canonical URL validator used by every klai service that fetches a
user-supplied URL:

- ``klai-knowledge-ingest`` wraps this in its own ``url_validator``
  module (backwards-compatible with the pre-SPEC ``validate_url``).
- ``klai-connector`` imports :func:`validate_image_url` and
  :class:`PinnedResolverTransport` for the adapter image pipeline.
- ``klai-portal`` keeps its own mirror at
  ``app.services.url_validator`` (no klai-libs dependency); the two
  implementations MUST track the same reject-list (REQ-4.2).

Closes:
- Finding #6 — preview_crawl SSRF parity (via knowledge-ingest wrapper)
- Finding #7 — persisted web_crawler connector SSRF (via portal mirror)
- Finding #8 — DNS-rebinding TOCTOU (:class:`ValidatedURL` pins the IP)
- Finding I — adapter image pipeline SSRF (:func:`validate_image_url`)

Key guarantees:

1. Every rejection raises :class:`SsrfBlockedError` (a ``ValueError``
   subclass, so pre-existing ``except ValueError`` call sites keep
   working).
2. Rejections are logged with stable fields (``event="ssrf_blocked"``,
   ``hostname``, ``reason``, ``resolved_ips``) so VictoriaLogs LogsQL
   ``event:"ssrf_blocked"`` returns every rejection across services.
3. :func:`validate_url_pinned` returns a :class:`ValidatedURL` carrying
   the resolved IP set; the caller uses :class:`PinnedResolverTransport`
   to connect to that IP even if DNS rebinds between guard and fetch.
4. An LRU cache (60 s TTL, 1024 entries) keeps the p95 added latency
   below the REQ-6.4 budget of 50 ms.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Reject-list constants
# ---------------------------------------------------------------------------

# Hostnames whose only resolution path is Docker's embedded DNS. These
# resolve to container IPs on internal bridges; any user-supplied URL
# pointing at one is SSRF by construction. REQ-1.3 / REQ-7.3.
DOCKER_INTERNAL_HOSTS: frozenset[str] = frozenset(
    {
        "portal-api",
        "docker-socket-proxy",
        "knowledge-ingest",
        "klai-connector",
        "klai-mailer",
        "mailer",
        "retrieval-api",
        "research-api",
        "klai-knowledge-mcp",
        "scribe",
        "scribe-api",
        "crawl4ai",
        "redis",
        "postgres",
        "qdrant",
        "falkordb",
        "litellm",
        "garage",
        "klai-focus",
    }
)


class Reason:
    """Stable reason codes for ``ssrf_blocked`` log entries (AC-11)."""

    NON_HTTPS = "non_https"
    NO_HOSTNAME = "no_hostname"
    PRIVATE_IP = "private_ip"
    LINK_LOCAL = "link_local"
    LOOPBACK = "loopback"
    RESERVED = "reserved"
    MULTICAST = "multicast"
    DOCKER_INTERNAL = "docker_internal"
    DNS_FAILED = "dns_failed"
    DNS_TIMEOUT = "dns_failed"  # aliased — same meaning, distinct cause
    DOMAIN_NOT_ALLOWED = "domain_not_allowed"


# REQ-8.1 / AC-19: Confluence connector ``base_url`` must live on an
# Atlassian-owned domain. The suffix list is authoritative for every
# klai service that validates a Confluence config — portal (create /
# update) and connector (legacy-row sync gate) both import from here.
ATLASSIAN_ALLOWED_SUFFIXES: tuple[str, ...] = (
    ".atlassian.net",
    ".atlassian.com",
)


def _is_ip_literal(host: str) -> bool:
    """Return True if *host* is a valid IP literal (v4 or v6).

    Uses :func:`ipaddress.ip_address` rather than a character-set
    heuristic. ``1.co`` and ``10.co`` are domains, not IP literals,
    and must go through the domain-allowlist check.
    """

    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedURL:
    """Outcome of a successful SSRF validation.

    Carries the resolved IP set so the subsequent HTTP fetch can
    connect to the exact IP the guard accepted (TOCTOU-safe,
    REQ-3.1). ``preferred_ip`` is what the pinned transport uses for
    the outbound connection; ``pinned_ips`` is the full set the guard
    resolved (multi-family or round-robin DNS).
    """

    url: str
    hostname: str
    pinned_ips: frozenset[str]
    preferred_ip: str


class SsrfBlockedError(ValueError):
    """Raised when a URL fails the SSRF guard.

    Subclasses ``ValueError`` so pre-SPEC call sites (``except
    ValueError: return 400``) keep working without modification.
    """

    def __init__(self, message: str, *, reason: str, hostname: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.hostname = hostname


# ---------------------------------------------------------------------------
# DNS cache (bounded LRU + TTL)
# ---------------------------------------------------------------------------


class _DnsCache:
    """Thread-safe LRU cache keyed by hostname.

    Keeps the p95 added latency under REQ-6.4's 50 ms budget. Entries
    expire after ``ttl_seconds`` and eviction is LRU once the cache
    grows past ``max_entries``. The cache is process-local — see
    research.md §9 open question on Redis persistence for multi-worker
    deploys (not required for knowledge-ingest's single-worker pattern
    today).
    """

    def __init__(self, max_entries: int = 1024, ttl_seconds: float = 60.0) -> None:
        self._entries: OrderedDict[str, tuple[tuple[str, ...], float]] = OrderedDict()
        self._max = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def get(self, hostname: str) -> tuple[str, ...] | None:
        with self._lock:
            entry = self._entries.get(hostname)
            if entry is None:
                return None
            ips, expiry = entry
            if time.monotonic() >= expiry:
                # Expired; drop and miss.
                self._entries.pop(hostname, None)
                return None
            self._entries.move_to_end(hostname)
            return ips

    def set(self, hostname: str, ips: tuple[str, ...]) -> None:
        with self._lock:
            expiry = time.monotonic() + self._ttl
            self._entries[hostname] = (ips, expiry)
            self._entries.move_to_end(hostname)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# Process-wide cache (safe to share — all entries are public DNS data).
_DEFAULT_CACHE = _DnsCache()


def _reset_dns_cache() -> None:
    """Clear the process-wide DNS cache.

    Internal helper — prefixed with ``_`` to signal test-only use.
    Production code should not reach into the cache; let the TTL
    expire naturally.
    """

    _DEFAULT_CACHE.clear()


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def classify_ip(ip: str) -> str | None:
    """Return a reject reason for *ip* or ``None`` if the IP is public.

    A non-parseable IP returns :attr:`Reason.RESERVED` because we
    cannot tell whether it is routable. This is the "fail closed"
    default demanded by REQ-3.5.
    """

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return Reason.RESERVED

    if addr.is_loopback:
        return Reason.LOOPBACK
    if addr.is_link_local:
        return Reason.LINK_LOCAL
    if addr.is_multicast:
        return Reason.MULTICAST
    # Reserved must come BEFORE is_private because IPv4 Class E
    # (240.0.0.0/4) is flagged by both predicates. We want
    # attackers using reserved ranges to get the specific
    # ``reserved`` reason code in ``event="ssrf_blocked"``.
    if addr.is_reserved or addr.is_unspecified:
        return Reason.RESERVED
    if addr.is_private:
        return Reason.PRIVATE_IP
    return None


def _hostname_is_docker_internal(hostname: str) -> bool:
    """Return True if *hostname* only resolves on a Docker bridge.

    Matches the reject-list even if Docker's embedded resolver returns
    a public-looking IP (AC-4 rationale).
    """

    host = hostname.lower().strip()
    # Bare hostname match — container names on the klai bridge have no dot.
    return host in DOCKER_INTERNAL_HOSTS


def _log_blocked(
    *,
    event: str | None,
    url: str,
    hostname: str | None,
    reason: str,
    resolved_ips: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit a ``warning`` with AC-11's stable field shape.

    The *event* arg is the structlog event name. Callers that will
    log a richer event themselves (with tenant context: ``org_id``,
    ``connector_id``, ...) pass ``event=None`` to suppress the
    generic entry — their own log replaces it 1-to-1, keeping
    VictoriaLogs LogsQL queries correlatable without duplicate hits.
    """

    if event is None:
        return
    fields: dict[str, Any] = {
        # Sanitise URL — drop query string per AC-11 spec.
        "url": url.split("?", 1)[0],
        "hostname": hostname,
        "reason": reason,
        "resolved_ips": resolved_ips or [],
    }
    if extra:
        fields.update(extra)
    # structlog uses the first positional arg as the ``event`` field.
    logger.warning(event, **fields)


# ---------------------------------------------------------------------------
# DNS resolution
# ---------------------------------------------------------------------------


def _resolve_blocking(hostname: str) -> tuple[str, ...]:
    """Resolve *hostname* to IPs using ``socket.getaddrinfo``.

    Returns the deduplicated tuple of addresses in the order the
    system resolver reported them (so IPv4 typically precedes IPv6
    depending on ``AI_ADDRCONFIG``).
    """

    infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
    seen: dict[str, None] = {}
    for info in infos:
        sockaddr = info[4]
        # sockaddr is (host, port) for IPv4 or (host, port, flowinfo,
        # scopeid) for IPv6 — host is always the first element and
        # always a str. Cast for pyright's narrowing.
        ip = str(sockaddr[0])
        seen.setdefault(ip, None)
    return tuple(seen.keys())


async def _resolve_async(hostname: str, timeout: float) -> tuple[str, ...]:
    """Run blocking DNS resolution in a thread to respect the event loop."""

    try:
        return await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _resolve_blocking, hostname),
            timeout=timeout,
        )
    except TimeoutError as exc:
        raise SsrfBlockedError(
            "DNS resolution timed out",
            reason=Reason.DNS_TIMEOUT,
            hostname=hostname,
        ) from exc
    except OSError as exc:
        raise SsrfBlockedError(
            f"DNS resolution failed: {exc}",
            reason=Reason.DNS_FAILED,
            hostname=hostname,
        ) from exc


# ---------------------------------------------------------------------------
# Core validators (async + sync)
# ---------------------------------------------------------------------------


def _parse_and_classify(url: str) -> tuple[str, str]:
    """Return ``(hostname, reason_if_rejected_else_empty)`` after URL parsing.

    Applies the scheme, hostname, and docker-internal checks that do
    NOT require DNS resolution. DNS-class checks happen in the
    resolver-aware entry points.
    """

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SsrfBlockedError(
            f"Only HTTPS URLs are allowed. Got: {parsed.scheme!r}",
            reason=Reason.NON_HTTPS,
            hostname=parsed.hostname,
        )
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        raise SsrfBlockedError(
            "URL has no hostname",
            reason=Reason.NO_HOSTNAME,
        )
    if _hostname_is_docker_internal(hostname):
        raise SsrfBlockedError(
            f"Hostname {hostname!r} is a docker-internal service",
            reason=Reason.DOCKER_INTERNAL,
            hostname=hostname,
        )
    # Literal IP hostnames bypass DNS — classify immediately so an
    # attacker cannot sneak ``https://10.0.0.5/`` past the guard
    # (AC-19's third bullet). Handled in caller after classification.
    return hostname, ""


def _classify_resolved(
    hostname: str,
    ips: tuple[str, ...],
    *,
    url: str,
) -> ValidatedURL:
    """Apply IP-class checks and return the pinned :class:`ValidatedURL`."""

    if not ips:
        raise SsrfBlockedError(
            f"DNS returned no addresses for {hostname!r}",
            reason=Reason.DNS_FAILED,
            hostname=hostname,
        )
    for ip in ips:
        reason = classify_ip(ip)
        if reason is not None:
            raise SsrfBlockedError(
                f"URL resolves to a forbidden address ({reason}): {ip}",
                reason=reason,
                hostname=hostname,
            )
    preferred = ips[0]
    return ValidatedURL(
        url=url,
        hostname=hostname,
        pinned_ips=frozenset(ips),
        preferred_ip=preferred,
    )


# @MX:ANCHOR: validate_url_pinned — canonical SSRF guard used by
#   knowledge-ingest (utils.url_validator), klai-connector
#   (services.url_guard, pipeline), and klai-portal
#   (services.url_validator). fan_in >= 3.
# @MX:REASON: Single source of truth for the reject-list. A bypass
#   in this function re-opens Findings #6 / #7 / #8 / I / II
#   simultaneously across all three services.
# @MX:SPEC: SPEC-SEC-SSRF-001
async def validate_url_pinned(
    url: str,
    *,
    dns_timeout: float = 2.0,
    cache: _DnsCache | None = None,
    log_as: str | None = "ssrf_blocked",
) -> ValidatedURL:
    """Validate *url* for SSRF and pin the resolved IP.

    Returns a :class:`ValidatedURL` on success. On rejection raises
    :class:`SsrfBlockedError` and emits a structured ``warning`` with
    the stable field shape from AC-11. DNS results are cached per
    hostname to meet the REQ-6.4 latency budget.

    Pass ``log_as=None`` when the caller will emit its own richer
    structured event (e.g. with ``org_id`` / ``connector_id``) —
    that prevents VictoriaLogs from recording two entries for the
    same rejection.
    """

    try:
        hostname, _ = _parse_and_classify(url)
    except SsrfBlockedError as exc:
        _log_blocked(event=log_as, url=url, hostname=exc.hostname, reason=exc.reason)
        raise

    # Literal IP hostnames: classify directly, no DNS lookup.
    try:
        ipaddress.ip_address(hostname)
        ips: tuple[str, ...] = (hostname,)
    except ValueError:
        c = cache or _DEFAULT_CACHE
        cached = c.get(hostname)
        if cached is not None:
            ips = cached
        else:
            try:
                ips = await _resolve_async(hostname, dns_timeout)
            except SsrfBlockedError as exc:
                _log_blocked(event=log_as, url=url, hostname=hostname, reason=exc.reason)
                raise
            c.set(hostname, ips)

    try:
        validated = _classify_resolved(hostname, ips, url=url)
    except SsrfBlockedError as exc:
        _log_blocked(
            event=log_as,
            url=url,
            hostname=hostname,
            reason=exc.reason,
            resolved_ips=list(ips),
        )
        raise
    return validated


def validate_url_pinned_sync(
    url: str,
    *,
    log_as: str | None = "ssrf_blocked",
) -> ValidatedURL:
    """Blocking variant for pydantic ``model_validator(mode="after")``.

    Pydantic validators run synchronously. We cannot ``await`` inside
    them, so the sync path bypasses the async thread pool and does a
    direct ``getaddrinfo``. The cache is still consulted so validation
    inside a busy request loop stays cheap.

    See :func:`validate_url_pinned` for the ``log_as`` semantics.
    """

    try:
        hostname, _ = _parse_and_classify(url)
    except SsrfBlockedError as exc:
        _log_blocked(event=log_as, url=url, hostname=exc.hostname, reason=exc.reason)
        raise

    try:
        ipaddress.ip_address(hostname)
        ips: tuple[str, ...] = (hostname,)
    except ValueError:
        cached = _DEFAULT_CACHE.get(hostname)
        if cached is not None:
            ips = cached
        else:
            try:
                ips = _resolve_blocking(hostname)
            except OSError as exc:
                _log_blocked(
                    event=log_as,
                    url=url,
                    hostname=hostname,
                    reason=Reason.DNS_FAILED,
                )
                raise SsrfBlockedError(
                    f"DNS resolution failed: {exc}",
                    reason=Reason.DNS_FAILED,
                    hostname=hostname,
                ) from exc
            _DEFAULT_CACHE.set(hostname, ips)

    try:
        validated = _classify_resolved(hostname, ips, url=url)
    except SsrfBlockedError as exc:
        _log_blocked(
            event=log_as,
            url=url,
            hostname=hostname,
            reason=exc.reason,
            resolved_ips=list(ips),
        )
        raise
    return validated


async def validate_image_url(url: str, *, dns_timeout: float = 2.0) -> ValidatedURL:
    """Image-pipeline alias for :func:`validate_url_pinned` (REQ-7.1).

    The reject-list is identical to the general guard (REQ-7.3). The
    generic log is suppressed (``log_as=None``) because the pipeline
    always emits its own ``adapter_image_ssrf_blocked`` event with
    the richer ``org_id`` / ``kb_slug`` context.
    """

    return await validate_url_pinned(url, dns_timeout=dns_timeout, log_as=None)


def validate_confluence_base_url(
    base_url: str,
    *,
    log_as: str | None = "ssrf_blocked",
) -> ValidatedURL:
    """Shared Confluence ``base_url`` validator (REQ-8).

    Used by both the portal pydantic ``ConfluenceConfig.model_validator``
    (create / update path) and the klai-connector adapter's load-time
    legacy-row gate. One source of truth for the allowlist — drift
    between services is structurally impossible.

    Applies, in order:

    1. HTTPS scheme + hostname presence (:func:`_parse_and_classify`).
    2. Docker-internal reject (same function).
    3. Atlassian domain allowlist — ``*.atlassian.net`` /
       ``*.atlassian.com``. IP literals are structurally skipped and
       fall through to (4) so an attacker cannot bypass the
       allowlist by writing ``https://10.0.0.5/`` — the SSRF check
       still rejects it.
    4. DNS resolution + IP reject-list (RFC1918, loopback, link-local,
       multicast, reserved).

    Raises :class:`SsrfBlockedError` on any failure; the caller wraps
    it into a pydantic ``ValueError`` (portal) or a
    ``PersistedUrlRejectedError`` (connector).
    """

    try:
        hostname, _ = _parse_and_classify(base_url)
    except SsrfBlockedError as exc:
        _log_blocked(event=log_as, url=base_url, hostname=exc.hostname, reason=exc.reason)
        raise

    # Atlassian allowlist — skipped for IP literals so the SSRF
    # reject-list classifies them by IP class instead. This matters
    # for ``https://10.0.0.5/``: the allowlist would never accept
    # it anyway, but by skipping here we produce the more precise
    # ``private_ip`` reason code instead of ``domain_not_allowed``.
    if not _is_ip_literal(hostname) and not any(hostname.endswith(suffix) for suffix in ATLASSIAN_ALLOWED_SUFFIXES):
        _log_blocked(
            event=log_as,
            url=base_url,
            hostname=hostname,
            reason=Reason.DOMAIN_NOT_ALLOWED,
        )
        raise SsrfBlockedError(
            "base_url must be on *.atlassian.net or *.atlassian.com",
            reason=Reason.DOMAIN_NOT_ALLOWED,
            hostname=hostname,
        )

    # Suppress the inner generic log — we already emitted one for
    # any pre-DNS rejection above, and the DNS path will log under
    # *log_as* too.
    return validate_url_pinned_sync(base_url, log_as=log_as)


# ---------------------------------------------------------------------------
# Pinned httpx transport
# ---------------------------------------------------------------------------


# @MX:ANCHOR: PinnedResolverTransport — DNS-rebinding defence used by
#   klai-connector SyncEngine (_image_http), klai-libs pipeline
#   (seeds pin map), and knowledge-ingest (imported via
#   utils.url_validator for future crawl4ai_client work). fan_in >= 3.
# @MX:REASON: Closes the TOCTOU window between the guard's DNS
#   resolution and httpx's subsequent getaddrinfo. Breaking the
#   pin-map contract (host missing, SNI dropped) silently reopens
#   Finding #8.
# @MX:SPEC: SPEC-SEC-SSRF-001
class PinnedResolverTransport(httpx.AsyncHTTPTransport):
    """httpx transport that connects to a pre-validated IP.

    Wraps :class:`httpx.AsyncHTTPTransport` and rewrites the outbound
    request URL's host to the pinned IP while preserving the
    ``Host`` header and TLS SNI (via ``extensions['sni_hostname']``).
    This closes the DNS-rebinding TOCTOU: validation and fetch see
    the same IP even if the attacker's authoritative DNS changes its
    answer between the two.

    Usage::

        pin_map: dict[str, str] = {}

        async def validate_then_fetch(url: str) -> httpx.Response:
            v = await validate_url_pinned(url)
            pin_map[v.hostname] = v.preferred_ip
            async with httpx.AsyncClient(
                transport=PinnedResolverTransport(pin_map),
            ) as client:
                return await client.get(url)

    The transport is stateful — the caller owns ``pin_map`` and
    updates it per request. A request whose host is absent from the
    map falls through to the default transport behaviour (DNS
    resolution), so the caller MUST set the map before the request
    or every unpinned request goes back to the TOCTOU-exposed path.
    """

    def __init__(
        self,
        pinned: dict[str, str] | None = None,
        **transport_kwargs: Any,
    ) -> None:
        super().__init__(**transport_kwargs)
        self._pinned: dict[str, str] = dict(pinned or {})
        self._lock = threading.Lock()

    def pin(self, hostname: str, ip: str) -> None:
        """Register *hostname* -> *ip* for subsequent requests."""

        with self._lock:
            self._pinned[hostname.lower()] = ip

    def unpin(self, hostname: str) -> None:
        """Remove *hostname* from the pin map if present."""

        with self._lock:
            self._pinned.pop(hostname.lower(), None)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        with self._lock:
            ip = self._pinned.get(host)
        if ip:
            # Rewrite the URL target host to the pinned IP; keep SNI +
            # Host header equal to the original hostname so TLS
            # verification and HTTP routing still see the name.
            # httpx preserves extensions across redirects, which keeps
            # the pin effective if the image server returns a 301/302
            # (research.md §11.6 open question).
            request.url = request.url.copy_with(host=ip)
            request.headers.setdefault("Host", host)
            # extensions['sni_hostname'] is honoured by httpx's HTTP/1
            # and HTTP/2 TLS paths; without it, SNI defaults to the IP
            # and cert validation fails.
            exts = dict(request.extensions) if request.extensions else {}
            exts["sni_hostname"] = host
            request.extensions = exts
        return await super().handle_async_request(request)


__all__ = [
    "ATLASSIAN_ALLOWED_SUFFIXES",
    "DOCKER_INTERNAL_HOSTS",
    "PinnedResolverTransport",
    "Reason",
    "SsrfBlockedError",
    "ValidatedURL",
    "classify_ip",
    "validate_confluence_base_url",
    "validate_image_url",
    "validate_url_pinned",
    "validate_url_pinned_sync",
]
