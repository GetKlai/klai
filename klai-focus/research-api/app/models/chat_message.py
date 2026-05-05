from datetime import datetime

from sqlalchemy import TIMESTAMP, VARCHAR, Column, Text
from sqlalchemy.dialects.postgresql import UUID

from app.models.notebook import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = {"schema": "research"}

    id = Column(VARCHAR(32), primary_key=True)
    notebook_id = Column(VARCHAR(32), nullable=False, index=True)
    # A-11: was VARCHAR(64) — migrated to UUID in 0004_chat_messages_uuid
    # to match notebooks/sources/chunks (all UUID). SPEC-TI-004-RLS-RESEARCH.
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    role = Column(VARCHAR(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
