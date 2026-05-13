"""Fixture P1 — full anti-pattern dict literal that maps profile roles to SeatType.

Should be flagged by ``rules/no-profile-derives-seat.yml``. Mirrors the
v0.1.0 mistake the SPEC explicitly retired: a single source-of-truth
table that derives the BILLING axis from the PERMISSION axis.
"""

from app.core.seats import SeatType


SEAT_FOR_ROLE_BROKEN = {
    "personal": SeatType.CHAT,
    "company": SeatType.CHAT,
    "kb_manager": SeatType.KNOWLEDGE,
    "group_manager": SeatType.KNOWLEDGE,
    "admin": SeatType.KNOWLEDGE,
}
