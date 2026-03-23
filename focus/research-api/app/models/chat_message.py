from datetime import datetime

from sqlalchemy import TIMESTAMP, VARCHAR, Column, Text

from app.models.notebook import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = {"schema": "research"}

    id = Column(VARCHAR(32), primary_key=True)
    notebook_id = Column(VARCHAR(32), nullable=False, index=True)
    tenant_id = Column(VARCHAR(64), nullable=False)
    role = Column(VARCHAR(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
