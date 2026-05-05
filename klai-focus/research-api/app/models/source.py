from datetime import datetime

from sqlalchemy import TIMESTAMP, VARCHAR, Column, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.notebook import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": "research"}

    id = Column(VARCHAR(32), primary_key=True)
    notebook_id = Column(
        VARCHAR(32),
        ForeignKey("research.notebooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # UUID matches the DB column type from 0001_create_research_schema.py.
    # SPEC-TI-004-RLS-RESEARCH: RLS tenant_isolation policy uses uuid comparison.
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    type = Column(VARCHAR(16), nullable=False)
    name = Column(Text, nullable=False)
    original_ref = Column(Text, nullable=True)
    file_path = Column(Text, nullable=True)
    status = Column(VARCHAR(16), nullable=False, default="pending")
    error_message = Column(Text, nullable=True)
    chunks_count = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)

    notebook = relationship("Notebook", back_populates="sources")
