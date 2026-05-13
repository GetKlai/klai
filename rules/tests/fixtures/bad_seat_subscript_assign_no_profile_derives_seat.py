"""Fixture P4 — subscript-assignment to SeatType.

Catches the "I'll just patch the table at runtime" workaround that
would otherwise sneak past a dict-literal lint.
"""

from app.core.seats import SeatType


SEAT_RUNTIME_MAP: dict[str, SeatType] = {}


def _populate(role: str) -> None:
    SEAT_RUNTIME_MAP[role] = SeatType.CHAT
