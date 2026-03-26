"""
Chat endpoint — SSE streaming with narrow/broad/web retrieval modes.
POST /v1/notebooks/{nb_id}/chat
"""
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.database import AsyncSessionLocal, get_db
from app.models.chat_message import ChatMessage
from app.models.notebook import Notebook
from app.services.retrieval import (
    BROAD_FOCUS_ONLY_SYSTEM_PROMPT,
    BROAD_SYSTEM_PROMPT,
    NARROW_SYSTEM_PROMPT,
    build_context,
    extract_citations,
    retrieve_broad_chunks,
    retrieve_chunks,
    retrieve_web_chunks,
    stream_llm,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest(BaseModel):
    question: str
    mode: str | None = None
    history: list[dict] | None = None


@router.post("/notebooks/{nb_id}/chat")
async def chat(
    nb_id: str,
    body: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    # Fetch notebook and verify access
    result = await db.execute(select(Notebook).where(Notebook.id == nb_id))
    nb: Notebook | None = result.scalar_one_or_none()
    if nb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")

    if nb.scope == "personal" and nb.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")
    if nb.scope == "org" and str(nb.tenant_id) != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")

    mode = body.mode or nb.default_mode or "narrow"
    if mode not in ("narrow", "broad", "web"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="mode moet 'narrow', 'broad' of 'web' zijn",
        )

    question = body.question.strip()
    history = body.history or []

    return StreamingResponse(
        _generate(db, question, mode, nb_id, user.tenant_id, history, nb.save_history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _generate(
    db: AsyncSession,
    question: str,
    mode: str,
    notebook_id: str,
    tenant_id: str,
    history: list[dict],
    save_history: bool,
):
    """Async generator that yields SSE lines."""
    full_response = ""
    try:
        # 1. Retrieve chunks based on mode
        if mode == "broad":
            doc_chunks = await retrieve_broad_chunks(db, question, notebook_id, tenant_id)
            web_chunks: list[dict] = []
        elif mode == "web":
            doc_chunks = await retrieve_chunks(db, question, notebook_id, tenant_id)
            web_chunks = await retrieve_web_chunks(question)
        else:  # narrow
            doc_chunks = await retrieve_chunks(db, question, notebook_id, tenant_id)
            web_chunks = []

        all_chunks = doc_chunks + web_chunks

        # 2. Build context and select system prompt
        context = build_context(all_chunks)
        if mode == "narrow":
            system_prompt = NARROW_SYSTEM_PROMPT
        elif mode == "broad":
            kb_available = any(c.get("origin") == "kb" for c in doc_chunks)
            system_prompt = BROAD_SYSTEM_PROMPT if kb_available else BROAD_FOCUS_ONLY_SYSTEM_PROMPT
        else:
            system_prompt = BROAD_SYSTEM_PROMPT

        # 4. Stream LLM tokens
        async for token in stream_llm(system_prompt, context, question, history):
            full_response += token
            yield _sse({"type": "token", "content": token})

        # 5. Emit done event with citations
        citations = extract_citations(doc_chunks)
        yield _sse({"type": "done", "citations": citations, "mode": mode})

        # 6. Persist messages if history is enabled
        if save_history and full_response:
            await _persist_messages(notebook_id, tenant_id, question, full_response)

    except Exception:
        logger.exception("Chat streaming error for notebook %s", notebook_id)
        yield _sse({"type": "error", "content": "Er is een fout opgetreden"})


async def _persist_messages(
    notebook_id: str, tenant_id: str, question: str, answer: str
) -> None:
    """Save user question and assistant answer to chat_messages using a fresh session."""
    try:
        async with AsyncSessionLocal() as db:
            for role, content in [("user", question), ("assistant", answer)]:
                db.add(
                    ChatMessage(
                        id="msg_" + uuid.uuid4().hex[:24],
                        notebook_id=notebook_id,
                        tenant_id=tenant_id,
                        role=role,
                        content=content,
                    )
                )
            await db.commit()
    except Exception:
        logger.exception("Failed to persist chat messages for notebook %s", notebook_id)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
