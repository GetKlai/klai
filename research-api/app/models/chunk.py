from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import TIMESTAMP, UUID, VARCHAR, Column, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.notebook import Base


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = {"schema": "research"}

    id = Column(VARCHAR(32), primary_key=True)
    source_id = Column(VARCHAR(32), nullable=False, index=True)
    notebook_id = Column(VARCHAR(32), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=True)
    embedding = Column(Vector(1024), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
