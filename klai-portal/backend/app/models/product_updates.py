from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductUpdate(Base):
    __tablename__ = "product_updates"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="ck_product_updates_title_nonempty"),
        CheckConstraint("length(btrim(body)) > 0", name="ck_product_updates_body_nonempty"),
        Index("ix_product_updates_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    commit_shas: Mapped[list[str]] = mapped_column(
        ARRAY(String(length=40)),
        nullable=False,
        default=list,
        server_default=text("'{}'::varchar[]"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProductUpdateRead(Base):
    __tablename__ = "product_update_reads"
    __table_args__ = (
        Index("ix_product_update_reads_user", "org_id", "user_id", "read_at"),
    )

    product_update_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("product_updates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    org_id: Mapped[int] = mapped_column(
        ForeignKey("portal_orgs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
