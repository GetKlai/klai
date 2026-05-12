"""Fixture P2 — partial anti-pattern dict with only a SUBSET of role keys.

Even one role -> SeatType entry must trip the lint; the anti-pattern
is the *coupling*, not the completeness of the ladder.
"""

from app.core.seats import SeatType


PARTIAL_BROKEN_SEAT_MAP = {
    "company": SeatType.CHAT,
}
