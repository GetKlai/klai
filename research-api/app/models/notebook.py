from datetime import datetime

from sqlalchemy import TIMESTAMP, UUID, VARCHAR, Column, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Notebook(Base):
    __tablename__ = "notebooks"
    __table_args__ = {"schema": "research"}

    id = Column(VARCHAR(32), primary_key=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    owner_user_id = Column(Text, nullable=False, index=True)
    scope = Column(VARCHAR(16), nullable=False, default="personal")
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    default_mode = Column(VARCHAR(16), nullable=False, default="narrow")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(
        TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    sources = relationship("Source", back_populates="notebook", lazy="select")
