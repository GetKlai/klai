"""
HTTP wrapper around klai-retrieval-api /retrieve for the RAGAS harness.

Authenticates with X-Internal-Secret. Returns RetrievalResult on success,
RetrievalFailure on any HTTP error, timeout, or connection error — never
raises so a single bad query does not abort the full suite run (REQ-3).

All timeouts default to settings.rag_eval_retrieval_timeout (10 s).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

# Maximum chars stored in RetrievalFailure.reason to keep DB meta rows small.
_MAX_REASON_LEN = 200


@dataclass
class RetrievalResult:
    """Successful retrieval outcome."""

    chunks: list[dict[str, Any]]
    retrieval_ms: int
    total_tokens: int | None = None


@dataclass
class RetrievalFailure:
    """Failed retrieval outcome — not an exception."""

    reason: str


def _build_client(
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Construct an httpx.AsyncClient with a fixed timeout and no retries."""
    kwargs: dict[str, Any] = {"timeout": timeout}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


async def retrieve_chunks(
    query: str,
    org_zitadel_id: str,
    user_zitadel_id: str | None,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> RetrievalResult | RetrievalFailure:
    """Call /retrieve on klai-retrieval-api and return chunks or a failure record.

    Parameters
    ----------
    query:
        The natural-language query to retrieve context for.
    org_zitadel_id:
        Tenant identifier used for scoped retrieval.
    user_zitadel_id:
        Optional user identifier; ``None`` for org-only scope.
    _transport:
        Optional httpx transport override — used in tests to inject mock responses
        without spinning up a real HTTP server.

    Returns
    -------
    RetrievalResult
        On HTTP 200 with a ``chunks`` list in the response body.
    RetrievalFailure
        On any HTTP error (status >= 400), timeout, or connection error.
    """
    url = f"{settings.retrieval_api_url}/retrieve"
    headers = {"X-Internal-Secret": settings.retrieval_internal_secret}
    body: dict[str, Any] = {
        "query": query,
        "org_id": org_zitadel_id,
        "user_id": user_zitadel_id,
        "conversation_history": [],
    }

    t0 = time.monotonic()
    try:
        async with _build_client(float(settings.rag_eval_retrieval_timeout), _transport) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
        reason = f"timeout: {exc}"[:_MAX_REASON_LEN]
        logger.warning("rag_eval_retrieval_timeout", query=query[:80], reason=reason)
        return RetrievalFailure(reason="timeout: retrieval call exceeded time limit")
    except httpx.HTTPStatusError as exc:
        reason = f"HTTP {exc.response.status_code}: {exc.response.text}"[:_MAX_REASON_LEN]
        logger.warning(
            "rag_eval_retrieval_http_error",
            status=exc.response.status_code,
            query=query[:80],
        )
        return RetrievalFailure(reason=reason)
    except (httpx.ConnectError, httpx.RequestError) as exc:
        reason = f"connection error: {exc}"[:_MAX_REASON_LEN]
        logger.warning("rag_eval_retrieval_connection_error", query=query[:80], reason=reason)
        return RetrievalFailure(reason=reason)

    retrieval_ms = int((time.monotonic() - t0) * 1000)
    chunks: list[dict[str, Any]] = data.get("chunks", [])
    total_tokens: int | None = data.get("total_tokens")

    return RetrievalResult(
        chunks=chunks,
        retrieval_ms=retrieval_ms,
        total_tokens=total_tokens,
    )
