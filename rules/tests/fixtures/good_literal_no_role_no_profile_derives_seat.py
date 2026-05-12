"""Fixture N2 — bare ``SeatType`` literal with NO role coupling.

A handler that hard-codes a tier for a specific code path (e.g. "this
endpoint always uses the CHAT-tier limits") is NOT the anti-pattern.
The rule only fires when the SeatType reference sits next to a role
string.
"""

from app.core.seats import SeatType


DEFAULT_TIER_FOR_TRIAL = SeatType.CHAT


def force_viewer_for_external_observer() -> SeatType:
    # Hard-coded viewer for a non-role-driven code path — fine.
    return SeatType.VIEWER
