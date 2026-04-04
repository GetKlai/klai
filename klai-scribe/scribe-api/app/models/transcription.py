from datetime import datetime

from sqlalchemy import JSON, NUMERIC, TEXT, TIMESTAMP, VARCHAR, Column
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Transcription(Base):
    __tablename__ = "transcriptions"
    __table_args__ = {"schema": "scribe"}

    id = Column(VARCHAR(64), primary_key=True)
    user_id = Column(VARCHAR(128), nullable=False, index=True)
    name = Column(VARCHAR(255), nullable=True)
    status = Column(VARCHAR(16), nullable=False, default="transcribed")
    audio_path = Column(VARCHAR(512), nullable=True)
    text = Column(TEXT, nullable=True)
    language = Column(VARCHAR(16), nullable=True)
    duration_seconds = Column(NUMERIC(8, 2), nullable=True)
    inference_time_seconds = Column(NUMERIC(8, 2), nullable=True)
    provider = Column(VARCHAR(64), nullable=True)
    model = Column(VARCHAR(128), nullable=True)
    summary_json = Column(JSON, nullable=True)
    recording_type = Column(VARCHAR(32), nullable=True)
    segments_json = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
