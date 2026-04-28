"""POST /chat endpoint -- SSE streaming retrieval + synthesis."""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from retrieval_api.config import settings
from retrieval_api.middleware.auth import verify_body_identity
from retrieval_api.models import RetrieveRequest
from retrieval_api.services import coreference, gate, reranker, search, synthesis
from retrieval_api.services.tei import embed_single

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat")
async def chat(req: RetrieveRequest, request: Request) -> EventSourceResponse:
    # --- Validation ---
    if req.scope in ("personal", "both") and not req.user_id:
        raise HTTPException(status_code=400, detail="user_id required for scope=personal/both")
    if req.scope == "notebook" and not req.notebook_id:
        raise HTTPException(status_code=400, detail="notebook_id required for scope=notebook")

    # SPEC-SEC-010 REQ-3 + SPEC-SEC-IDENTITY-ASSERT-001 REQ-4: cross-user /
    # cross-org guard. JWT callers are matched against their JWT claims;
    # internal-secret callers are re-verified against portal-api so the
    # internal-secret bypass no longer admits arbitrary body identities.
    await verify_body_identity(request, req.org_id, req.user_id)

    async def event_generator():
        t0 = time.perf_counter()

        # 1. Coreference resolution
        query_resolved = await coreference.resolve(req.query, req.conversation_history)

        # 2. Embed resolved query
        query_vector = await embed_single(query_resolved)

        # 3. Gate check
        bypassed, gate_margin = await gate.should_bypass(query_vector)

        if bypassed:
            # Stream a bypass done event with no tokens
            done_event = {
                "type": "done",
                "citations": [],
                "retrieval_bypassed": True,
                "query_resolved": query_resolved,
            }
            yield json.dumps(done_event)
            return

        # 4. Search
        raw_results = await search.hybrid_search(query_vector, req, settings.retrieval_candidates)

        # 5. Rerank (skip for notebook scope)
        if req.scope != "notebook" and raw_results:
            reranked = await reranker.rerank(query_resolved, raw_results, req.top_k)
        else:
            reranked = raw_results[: req.top_k]

        retrieval_ms = (time.perf_counter() - t0) * 1000

        # Structured logging
        logger.info(
            "chat",
            extra={
                "org_id": req.org_id,
                "scope": req.scope,
                "top_k": req.top_k,
                "candidates_retrieved": len(raw_results),
                "retrieval_ms": round(retrieval_ms, 1),
                "gate_margin": round(gate_margin, 4) if gate_margin is not None else None,
                "retrieval_bypassed": False,
            },
        )

        # 6. Stream synthesis
        async for item in synthesis.synthesize(query_resolved, reranked, req.conversation_history):
            if isinstance(item, str):
                yield json.dumps({"type": "token", "content": item})
            elif isinstance(item, dict):
                # Final event
                done_event = {
                    "type": "done",
                    "citations": item.get("citations", []),
                    "retrieval_bypassed": item.get("retrieval_bypassed", False),
                    "query_resolved": item.get("query_resolved", query_resolved),
                }
                yield json.dumps(done_event)

    return EventSourceResponse(event_generator())
