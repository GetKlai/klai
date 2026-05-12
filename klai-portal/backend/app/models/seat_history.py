"""Per-user seat-change history — SPEC-PORTAL-PRICING-PER-USER-001 Phase 1.

Append-only audit table that snapshots every transition of
``portal_users.seat_type | role | status``. Phase 5 prorate billing reads
this table to compute "Roman was on KNOWLEDGE for 12 days, then CHAT for
18 days this month"; Phase 1 only writes to it (read endpoint is the
admin breakdown, which aggregates ``portal_users`` directly).

Write path is a Postgres ``BEFORE/AFTER INSERT OR UPDATE`` trigger on
``portal_users`` (NOT a SQLAlchemy event listener). This is intentional:

  - Trigger fires on raw ``UPDATE portal_users SET ...`` from any session,
    including admin scripts that bypass the ORM via
    ``session.execute(update(...).values(...))``.
  - Trigger runs in the same transaction as the parent UPDATE, so a
    rollback also rolls back the history row.
  - The partial-unique index ``idx_pu_seat_hist_one_open_per_user``
    enforces "at most one open row per user" at the DB layer — concurrent
    UPDATEs serialize cleanly instead of racing into overlapping rows.

RLS policy lives in the post-deploy SQL file (Cat-D pattern with the
schema-qualified ``billing._rls_current_org_id()`` helper, see
``alembic-cannot-drop-non-portal_api-tables`` pitfall and
``postgres-no-return-type-overload`` pitfall in process-rules.md).

@MX:NOTE Phase 1 ships the table and trigger; Phase 5 introduces the
prorate query consumer.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortalUserSeatHistory(Base):
    """Append-only history row for one (user, seat_type, role, status) tuple.

    Lifecycle:
        - ``valid_from`` = NOW() at the moment the row is inserted.
        - ``valid_to``   = NULL while this row reflects the user's CURRENT
                            seat/role/status. The trigger sets it to NOW()
                            when the next change lands.
        - ``change_reason`` is one of:
            ``'invite'``        -- INSERT of the parent portal_users row.
            ``'seat_change'``   -- seat_type changed.
            ``'role_change'``   -- role changed.
            ``'status_change'`` -- status changed.
            ``'backfill'``      -- one-time row inserted by the Phase 1
                                    migration for every existing user.

    The DB CHECK constraints + partial-unique index protect the invariants;
    the ORM model is a read-only convenience for tests and admin tooling.
    """

    __tablename__ = "portal_user_seat_history"
    __table_args__ = (
        CheckConstraint(
            "seat_type IN ('viewer', 'chat', 'knowledge')",
            name="ck_pu_seat_hist_seat_type",
        ),
        Index("idx_pu_seat_hist_user_validto", "user_id", "valid_to"),
        Index("idx_pu_seat_hist_org_validfrom", "org_id", "valid_from"),
        # Partial unique: at most ONE open (current) row per user at a
        # time. Concurrent UPDATEs on portal_users serialize through this
        # index; the trigger relies on it to close the previous current
        # row before inserting the new one.
        Index(
            "idx_pu_seat_hist_one_open_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("portal_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("portal_orgs.id"),
        nullable=False,
    )
    seat_type: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    # v0.4.0: status snapshot lets Phase 5 prorate scope to billable rows
    # ('active' only). Values come from portal_users.status (CHECK
    # constraint: 'active' | 'suspended' | 'offboarded'). No CHECK on
    # this column on purpose — a future ladder rename in portal_users
    # must not retroactively invalidate historic rows. Phase 5 prorate
    # query filters ``status = 'active'`` explicitly.
    status: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
