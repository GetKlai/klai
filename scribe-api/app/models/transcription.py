from datetime import datetime

from sqlalchemy import NUMERIC, TEXT, TIMESTAMP, VARCHAR, Column
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Transcription(Base):
    __tablename__ = "transcriptions"
    __table_args__ = {"schema": "scribe"}

    id = Column(VARCHAR(64), primary_key=True)
    user_id = Column(VARCHAR(128), nullable=False, index=True)
    text = Column(TEXT, nullable=False)
    language = Column(VARCHAR(16), nullable=False)
    duration_seconds = Column(NUMERIC(8, 2), nullable=False)
    inference_time_seconds = Column(NUMERIC(8, 2), nullable=False)
    provider = Column(VARCHAR(64), nullable=False)
    model = Column(VARCHAR(128), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
