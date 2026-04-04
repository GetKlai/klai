"""Trace context helpers for cross-service request correlation.

Reads request_id and org_id from structlog contextvars (bound by
LoggingContextMiddleware) and returns them as HTTP headers for
outgoing httpx calls to downstream services.
"""

from __future__ import annotations

import structlog


def get_trace_headers() -> dict[str, str]:
    """Return trace context headers for inter-service HTTP calls.

    Downstream services read these headers to correlate logs across
    the request chain: portal-api -> knowledge-ingest -> retrieval-api.
    """
    ctx = structlog.contextvars.get_contextvars()
    headers: dict[str, str] = {}
    if request_id := ctx.get("request_id"):
        headers["X-Request-ID"] = str(request_id)
    if org_id := ctx.get("org_id"):
        headers["X-Org-ID"] = str(org_id)
    return headers
