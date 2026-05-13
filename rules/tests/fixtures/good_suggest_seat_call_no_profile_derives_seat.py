"""Fixture N1 — uses the canonical ``suggest_seat()`` helper.

This is the GREEN path: smart-default for the common case, admin
override sets seat_type independently via the explicit selector. NO
inline role -> SeatType mapping happens here.
"""

from app.core.seats import SeatType, suggest_seat


def assign_seat_on_invite(role: str) -> SeatType:
    return suggest_seat(role)
