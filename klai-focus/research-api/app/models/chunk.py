from datetime import datetime

from sqlalchemy import TIMESTAMP, VARCHAR, Column, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.notebook import Base


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = {"schema": "research"}

    id = Column(VARCHAR(32), primary_key=True)
    source_id = Column(VARCHAR(32), nullable=False, index=True)
    notebook_id = Column(VARCHAR(32), nullable=False)
    # UUID matches the DB column type from 0001_create_research_schema.py.
    # SPEC-TI-004-RLS-RESEARCH: RLS tenant_isolation policy uses uuid comparison.
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    content = Column(Text, nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
