"""Telemetry emission helpers — extracted from deploy/litellm/klai_knowledge.py.

SPEC-MCP-RETRIEVAL-001 Phase 1: behaviour-preserving extraction of the three
fire-and-forget POST helpers and the gap-classifier so both the LiteLLM
pre-call hook (LibreChat) and klai-knowledge-mcp (third-party LLMs via
OAuth) post the same payload shape against the same portal-api endpoints.

The single contract addition over the original inline impls is the optional
``caller_client_id`` keyword argument on the two ``fire_*`` helpers. When
``None`` (the LibreChat default), the payload omits the field — same wire
shape as before. When set, it labels the row with the OAuth client that
issued the call.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# -- Configuration ------------------------------------------------------------
# Module-level defaults loaded from env on first ``_default_config()`` call.
# Tests can pass their own ``RetrievalTelemetryConfig`` to bypass env entirely.
@dataclass(frozen=True, slots=True)
class RetrievalTelemetryConfig:
    """Connection + behaviour config for the telemetry helpers.

    Defaults match the LiteLLM-hook env-driven values so existing callers
    pick up identical behaviour without explicit construction. Tests should
    construct a config directly to avoid env-leak between cases.
    """

    portal_api_url: str = ""
    portal_internal_secret: str = ""
    portal_retrieval_log_url: str = ""
    portal_gap_events_url: str = ""
    embedding_model_version: str = "bge-m3-v1"
    gap_soft_threshold: float = 0.4
    gap_dense_threshold: float = 0.35
    timeout_seconds: float = 2.0
    # @MX:NOTE: if you add a field here, mirror it in _default_config below
    # so the env path stays in sync with the dataclass shape.
    _frozen: bool = field(default=True, init=False, repr=False)


def _default_config() -> RetrievalTelemetryConfig:
    """Build a config from env vars. Caller-overridable per call.

    Reads on every call rather than at import time so containers that
    rotate ``PORTAL_INTERNAL_SECRET`` via SOPS sync pick up the new
    secret without a process restart. The cost (a few env-dict lookups)
    is negligible compared to the HTTP POST that follows.
    """
    portal_api_url = os.getenv("PORTAL_API_URL", "")
    portal_secret = os.getenv("PORTAL_INTERNAL_SECRET", "")
    return RetrievalTelemetryConfig(
        portal_api_url=portal_api_url,
        portal_internal_secret=portal_secret,
        portal_retrieval_log_url=os.getenv(
            "PORTAL_RETRIEVAL_LOG_URL",
            f"{portal_api_url}/internal/v1/retrieval-log",
        ),
        portal_gap_events_url=os.getenv(
            "PORTAL_GAP_EVENTS_URL",
            f"{portal_api_url}/internal/v1/gap-events",
        ),
        embedding_model_version=os.getenv("EMBEDDING_MODEL_VERSION", "bge-m3-v1"),
        gap_soft_threshold=float(os.getenv("KLAI_GAP_SOFT_THRESHOLD", "0.4")),
        gap_dense_threshold=float(os.getenv("KLAI_GAP_DENSE_THRESHOLD", "0.35")),
    )


# -- Gap classification -------------------------------------------------------
# @MX:NOTE: Gap thresholds (0.4 reranker, 0.35 dense) are configurable via
# KLAI_GAP_SOFT_THRESHOLD / KLAI_GAP_DENSE_THRESHOLD env vars (SPEC-KB-014)
def classify_gap(
    chunks: list[dict[str, Any]],
    config: RetrievalTelemetryConfig | None = None,
) -> str | None:
    """Classify retrieval result. Returns 'hard', 'soft', or None (success).

    - ``hard``: empty chunk list — retrieval found nothing.
    - ``soft``: chunks present but all reranker (or dense, when reranker
      absent) scores fall below the configured threshold.
    - ``None``: at least one chunk crossed the threshold.

    Pure function — no I/O. Safe to call inside a hot path.
    """
    cfg = config or _default_config()
    if not chunks:
        return "hard"
    reranker_scores = [
        c.get("reranker_score") for c in chunks if c.get("reranker_score") is not None
    ]
    if reranker_scores:
        if all(s < cfg.gap_soft_threshold for s in reranker_scores):
            return "soft"
    else:
        dense_scores = [c.get("score", 0.0) for c in chunks]
        if all(s < cfg.gap_dense_threshold for s in dense_scores):
            return "soft"
    return None


# -- Retrieval-log emit -------------------------------------------------------
# @MX:NOTE: Fire-and-forget retrieval log -- mirrors fire_gap_event pattern. SPEC-KB-015.
# @MX:WARN: Uses create_task -- caller must be inside running event loop.
# @MX:REASON: Silently discards on no-loop (test context) and any HTTP error (REQ-KB-015-03).
def fire_retrieval_log(
    org_id: str,
    user_id: str,
    chunk_ids: list[str],
    reranker_scores: list[float],
    query_resolved: str,
    *,
    caller_client_id: str | None = None,
    config: RetrievalTelemetryConfig | None = None,
) -> None:
    """Schedule an async retrieval log POST without blocking the caller.

    Behaviour-equivalent to the previous inline impl in
    ``deploy/litellm/klai_knowledge.py``. The optional
    ``caller_client_id`` keyword adds OAuth-client attribution per
    SPEC-MCP-RETRIEVAL-001 REQ-9; when ``None`` the payload key is
    omitted, preserving the wire shape for LibreChat traffic.
    """
    cfg = config or _default_config()

    try:
        int(org_id)
    except (ValueError, TypeError):
        logger.warning(
            "retrieval_telemetry: non-numeric org_id '%s', skipping retrieval log", org_id
        )
        return

    payload: dict[str, Any] = {
        "org_id": str(org_id),
        "user_id": user_id,
        "chunk_ids": chunk_ids,
        "reranker_scores": reranker_scores,
        "query_resolved": query_resolved,
        "embedding_model_version": cfg.embedding_model_version,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    if caller_client_id is not None:
        payload["caller_client_id"] = caller_client_id

    async def _post() -> None:
        try:
            async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
                await client.post(
                    cfg.portal_retrieval_log_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {cfg.portal_internal_secret}"},
                )
        except Exception as exc:  # noqa: BLE001 — fire-and-forget, swallow all
            logger.warning("retrieval_telemetry: retrieval log POST failed (%s)", exc)

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        # No running event loop (test context) — skip silently
        pass


# -- Gap-event emit -----------------------------------------------------------
# @MX:WARN: Fire-and-forget via create_task — caller must be inside a running event loop.
# @MX:REASON: Wraps in try/except RuntimeError to handle test environments without a loop.
def fire_gap_event(
    org_id: str,
    user_id: str,
    query_text: str,
    gap_type: str,
    chunks: list[dict[str, Any]],
    retrieval_ms: int,
    taxonomy_node_ids: list[int] | None = None,
    *,
    caller_client_id: str | None = None,
    config: RetrievalTelemetryConfig | None = None,
) -> None:
    """Schedule an async gap event POST without blocking the caller.

    Behaviour-equivalent to the previous inline impl in
    ``deploy/litellm/klai_knowledge.py``. The optional
    ``caller_client_id`` keyword adds OAuth-client attribution per
    SPEC-MCP-RETRIEVAL-001 REQ-9; when ``None`` the payload key is
    omitted, preserving the wire shape for LibreChat traffic.
    """
    cfg = config or _default_config()

    top_chunk = (
        max(chunks, key=lambda c: c.get("reranker_score") or c.get("score", 0.0))
        if chunks
        else None
    )
    top_score = (
        (top_chunk.get("reranker_score") or top_chunk.get("score"))
        if top_chunk
        else None
    )
    nearest_kb_slug = (
        top_chunk.get("metadata", {}).get("kb_slug")
        if top_chunk and gap_type == "soft"
        else None
    )

    try:
        org_id_int = int(org_id)
    except (ValueError, TypeError):
        logger.warning(
            "retrieval_telemetry: non-numeric org_id '%s', skipping gap event", org_id
        )
        return

    payload: dict[str, Any] = {
        "org_id": org_id_int,
        "user_id": user_id,
        "query_text": query_text,
        "gap_type": gap_type,
        "top_score": top_score,
        "nearest_kb_slug": nearest_kb_slug,
        "chunks_retrieved": len(chunks),
        "retrieval_ms": retrieval_ms,
    }
    # SPEC-KB-021 R6: include taxonomy filter when it was part of the retrieve request
    if taxonomy_node_ids:
        payload["taxonomy_node_ids"] = taxonomy_node_ids
    if caller_client_id is not None:
        payload["caller_client_id"] = caller_client_id

    async def _post() -> None:
        try:
            async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
                await client.post(
                    cfg.portal_gap_events_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {cfg.portal_internal_secret}"},
                )
        except Exception as exc:  # noqa: BLE001 — fire-and-forget, swallow all
            logger.warning("retrieval_telemetry: gap event POST failed (%s)", exc)

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        # No running event loop (test context) — skip silently
        pass
