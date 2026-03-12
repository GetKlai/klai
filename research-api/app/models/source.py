from datetime import datetime

from sqlalchemy import TIMESTAMP, UUID, VARCHAR, Column, Integer, Text
from sqlalchemy.orm import relationship

from app.models.notebook import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = {"schema": "research"}

    id = Column(VARCHAR(32), primary_key=True)
    notebook_id = Column(VARCHAR(32), nullable=False, index=True)
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
