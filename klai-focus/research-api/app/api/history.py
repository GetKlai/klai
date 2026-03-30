"""
Chat history endpoints:
  GET    /v1/notebooks/{nb_id}/history
  DELETE /v1/notebooks/{nb_id}/history
"""
from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.models.chat_message import ChatMessage
from app.models.notebook import Notebook
from app.api.notebooks import _get_notebook_or_404

router = APIRouter(prefix="/v1", tags=["history"])


class ChatMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class HistoryResponse(BaseModel):
    items: list[ChatMessageResponse]


@router.get("/notebooks/{nb_id}/history", response_model=HistoryResponse)
async def get_history(
    nb_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    await _get_notebook_or_404(nb_id, db, user)

    rows = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.notebook_id == nb_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = rows.scalars().all()

    return HistoryResponse(
        items=[
            ChatMessageResponse(
                id=msg.id,
                role=msg.role,
                content=msg.content,
                created_at=msg.created_at,
            )
            for msg in messages
        ]
    )


@router.delete("/notebooks/{nb_id}/history", status_code=status.HTTP_204_NO_CONTENT)
async def delete_history(
    nb_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_notebook_or_404(nb_id, db, user)

    await db.execute(delete(ChatMessage).where(ChatMessage.notebook_id == nb_id))
    await db.commit()
