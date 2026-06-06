from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlatformMessageThread(Base):
    __tablename__ = "platform_message_threads"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_platform_message_threads_status"),
        CheckConstraint(
            "origin_type IN ('direct', 'feedback_submission', 'feedback_item')",
            name="ck_platform_message_threads_origin_type",
        ),
        Index("ix_platform_message_threads_org_status", "org_id", "status", "last_message_at"),
        Index("ix_platform_message_threads_last_message", "last_message_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    origin_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'direct'"))
    feedback_submission_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("feedback_submissions.id", ondelete="SET NULL"),
        nullable=True,
    )
    feedback_item_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("feedback_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PlatformMessageParticipant(Base):
    __tablename__ = "platform_message_participants"
    __table_args__ = (
        Index("ix_platform_message_participants_user", "org_id", "user_id", "created_at"),
        Index("ix_platform_message_participants_thread", "thread_id"),
    )

    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("platform_message_threads.id", ondelete="CASCADE"),
        primary_key=True,
    )
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PlatformMessage(Base):
    __tablename__ = "platform_messages"
    __table_args__ = (
        CheckConstraint("sender_type IN ('platform_admin', 'user', 'system')", name="ck_platform_messages_sender_type"),
        Index("ix_platform_messages_thread_created", "thread_id", "created_at"),
        Index("ix_platform_messages_org_created", "org_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("platform_message_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sender_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
