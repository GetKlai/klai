"""Per-user seat model — SPEC-PORTAL-PRICING-PER-USER-001 (Phase 1).

Klai's website (getklai.com/pricing) sells per-user seat pricing:
``Klai Chat`` €28/user/mo and ``Klai Chat + Knowledge`` €68/user/mo.
Phase 1 introduces the **billing axis** as an orthogonal per-user
attribute (``portal_users.seat_type``) and the read-only billing
breakdown endpoint. Permission resolution still flows through
``app/core/profiles.py::PROFILE_CAPABILITIES`` for now — the seat
intersection only swaps in at Phase 4.

Two orthogonal user attributes:

  seat_type:  ``viewer | chat | knowledge``    -- billing + feature unlock
  role:       ``personal | company | kb_manager | group_manager | admin``
                                              -- permissions (existing)

This module is the SINGLE source of truth for:

  - ``SeatType`` enum and the three valid string values
  - ``SEAT_FEATURES``   -- which product surfaces each seat unlocks
  - ``CAPABILITY_TO_SEAT_FEATURE`` -- which capability requires which feature
  - ``SEAT_PRICE_MONTHLY`` / ``SEAT_PRICE_YEARLY_MONTH_EQUIV`` -- pricing
  - ``DEFAULT_SEAT_FOR_ROLE`` -- smart-default mapping (admin can override)
  - ``suggest_seat(role)`` -- the ONLY allowed PROFILE -> SeatType mapping
                              outside this module; AC-13 ast-grep blocks
                              any other site.

@MX:ANCHOR fan_in=3+ -- canonical seats catalogue. New SeatType members or
                        SEAT_FEATURES entries land here; downstream code
                        reads via these names and is NOT allowed to inline
                        a role -> SeatType dict literal.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.profiles import PROFILE_CAPABILITIES, PROFILE_RANK

# ---------------------------------------------------------------------------
# Seat enum
# ---------------------------------------------------------------------------


class SeatType(StrEnum):
    """The three Klai seat tiers.

    ``VIEWER`` is billing-only: €0/mo, read-only access enforced via the
    absence of write-capabilities in ``SEAT_FEATURES`` + frontend rendering
    on ``effective_features``. Phase 1 backfill produces zero viewer-users;
    Phase 2 adds explicit ``chat.read``/``kb.read`` capabilities and the
    seat-selector UI.
    """

    VIEWER = "viewer"
    CHAT = "chat"
    KNOWLEDGE = "knowledge"


# ---------------------------------------------------------------------------
# Feature unlock per seat
# ---------------------------------------------------------------------------

# What product surfaces each seat type unlocks.
#
# v0.4.0: viewer is billing-only. The strings ``chat_readonly`` /
# ``knowledge_readonly`` are FE-rendering hints (sidebar shows the surfaces
# in read-only mode), NOT capabilities. Backend write-endpoints enforce
# capability requirements; a viewer has none of those (see
# ``effective_capabilities`` below), so endpoint-level access falls back to
# 403 even if the FE were bypassed. Phase 2 adds explicit
# ``chat.read``/``kb.read`` capabilities for full symmetry.
#
# scribe + docs are INCLUDED in both paid seats. The role-floor
# (``FEATURE_MIN_PROFILE`` in core/features.py: ``scribe -> company``,
# ``docs -> company``) keeps personal-role users out — they don't see
# scribe/docs in the sidebar even if their seat unlocks them.
SEAT_FEATURES: dict[SeatType, frozenset[str]] = {
    SeatType.VIEWER: frozenset({"chat_readonly", "knowledge_readonly"}),
    SeatType.CHAT: frozenset(
        {
            "chat",
            "knowledge.basic",
            "kb.connectors",
            "scribe",
            "docs",
        }
    ),
    SeatType.KNOWLEDGE: frozenset(
        {
            "chat",
            "knowledge.basic",
            "knowledge.full",
            "kb.connectors",
            "kb.connectors.external",
            "kb.create_org",
            "kb.members",
            "kb.taxonomy",
            "kb.gaps",
            "scribe",
            "docs",
        }
    ),
}


# Maps each capability string back to the seat-feature it requires.
# Explicit mapping; no implicit prefix-matching, no convention. If a new
# capability lands in ``Capability`` (core/profiles.py) without an entry
# here, the seats-module test catches it (every Capability member must
# appear as a key OR be explicitly opted out via the test allow-list).
CAPABILITY_TO_SEAT_FEATURE: dict[str, str] = {
    # Connector capabilities
    "kb.connectors": "kb.connectors",
    "kb.connectors.external": "kb.connectors.external",
    # KB management — all unlocked by knowledge.full
    "kb.create_org": "knowledge.full",
    "kb.members": "knowledge.full",
    "kb.taxonomy": "knowledge.full",
    "kb.gaps": "knowledge.full",
}


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

# Monthly rate per seat in EUR. Source of truth for Phase 5 Moneybird
# line-items; mirrors getklai.com/pricing.
SEAT_PRICE_MONTHLY: dict[SeatType, int] = {
    SeatType.VIEWER: 0,
    SeatType.CHAT: 28,
    SeatType.KNOWLEDGE: 68,
}

# Yearly-discount rate, expressed as the *equivalent per-month price* on
# an annual contract. €20/mo means €240/yr billed annually. €48/mo means
# €576/yr billed annually.
SEAT_PRICE_YEARLY_MONTH_EQUIV: dict[SeatType, int] = {
    SeatType.VIEWER: 0,
    SeatType.CHAT: 20,
    SeatType.KNOWLEDGE: 48,
}


# ---------------------------------------------------------------------------
# Smart default — the ONLY allowed PROFILE -> SeatType mapping outside this
# module (AC-13 ast-grep blocks duplicates).
# ---------------------------------------------------------------------------

# Default seat suggested when admin invites a user with a given role.
# Personal/company (chat workloads) -> CHAT seat (€28/mo).
# kb_manager/group_manager/admin (KB workloads) -> KNOWLEDGE seat (€68/mo).
# Admin can override the suggestion in the invite UI (Phase 2).
DEFAULT_SEAT_FOR_ROLE: dict[str, SeatType] = {
    "personal": SeatType.CHAT,
    "company": SeatType.CHAT,
    "kb_manager": SeatType.KNOWLEDGE,
    "group_manager": SeatType.KNOWLEDGE,
    "admin": SeatType.KNOWLEDGE,
}


def suggest_seat(role: str) -> SeatType:
    """Return the suggested seat for a given role.

    Default for unknown roles is ``CHAT`` (cheapest non-zero tier — refuses
    to silently produce a free viewer-seat from a malformed role string).
    Admin can always override via the explicit seat selector.

    This is the SINGLE allowed PROFILE -> SeatType mapping outside this
    module. AC-13 ast-grep blocks any other call site from inlining a
    role -> ``SeatType.<X>`` dict literal.
    """
    return DEFAULT_SEAT_FOR_ROLE.get(role, SeatType.CHAT)


# ---------------------------------------------------------------------------
# Effective access composition
# ---------------------------------------------------------------------------


def effective_features(seat_type: SeatType, role: str) -> frozenset[str]:
    """Return the set of product surfaces this (seat, role) combo unlocks.

    Composition rule:
      1. Start with ``SEAT_FEATURES[seat_type]`` (the seat's unlocked set).
      2. For each feature, look up its role-floor in
         ``core/features.FEATURE_MIN_PROFILE`` (default ``personal``).
      3. Drop the feature if the caller's role-rank is below the floor.

    v0.4.0: ``PROFILE_RANK.get(role, -1)`` defends against unknown role
    strings (e.g. a legacy snapshot value in ``portal_user_seat_history``).
    Unknown role -> rank -1 -> nothing unlocked. Fail-closed.
    """
    # Local import: features.py imports from profiles.py, and this module
    # also imports from profiles.py. Lazy import here avoids a future
    # circular-import risk if features.py later imports from seats.py.
    from app.core.features import FEATURE_MIN_PROFILE

    seat_unlocked = SEAT_FEATURES[seat_type]
    caller_rank = PROFILE_RANK.get(role, -1)
    return frozenset(
        feature
        for feature in seat_unlocked
        if caller_rank >= PROFILE_RANK.get(FEATURE_MIN_PROFILE.get(feature, "personal"), -1)
    )


def effective_capabilities(role: str, seat_type: SeatType) -> frozenset[str]:
    """Return the capabilities this (role, seat) combo grants.

    Composition rule:
      1. Start with ``PROFILE_CAPABILITIES[role]`` (role-granted caps).
      2. For each capability, look up its required seat-feature in
         ``CAPABILITY_TO_SEAT_FEATURE``.
      3. Drop the capability if the seat does not unlock that feature.

    Examples (Phase 1 — full swap to this comes in Phase 4):
      - ``kb_manager`` + ``KNOWLEDGE`` -> all 6 capabilities (knowledge.full
        unlocks the four KB-management caps; both connector caps unlocked).
      - ``kb_manager`` + ``CHAT``      -> only ``kb.connectors`` (knowledge.full
        not unlocked by chat seat, and ``kb.connectors.external`` requires
        the external feature which chat-seat lacks).
      - ``admin`` + ``VIEWER``         -> ``frozenset()`` (no mapped capability
        is unlocked by viewer). Admin retains role-rank powers through
        ``_require_at_least('admin')``, which is INDEPENDENT of capability
        gating — admin-on-viewer is the €0 board-observer pattern.

    Phase 4 will swap the existing
    ``effective_kb_limits(role, plan).capabilities`` to call this function;
    Phase 1 ships the helper without wiring it in.
    """
    role_caps = PROFILE_CAPABILITIES.get(role, frozenset())
    seat_unlocked = SEAT_FEATURES[seat_type]
    return frozenset(cap for cap in role_caps if CAPABILITY_TO_SEAT_FEATURE.get(cap) in seat_unlocked)


def monthly_seat_cost(seat_type: SeatType, *, yearly_contract: bool = False) -> int:
    """Return the per-seat monthly price in EUR.

    ``yearly_contract=True`` returns the *equivalent monthly* price under an
    annual contract (the yearly discount). Returns 0 for VIEWER regardless.
    """
    table = SEAT_PRICE_YEARLY_MONTH_EQUIV if yearly_contract else SEAT_PRICE_MONTHLY
    return table[seat_type]


def breakdown_to_monthly_bill(breakdown: dict[SeatType | str, int], *, yearly_contract: bool = False) -> int:
    """Sum a ``{seat_type: count}`` breakdown into a monthly total.

    Accepts either ``SeatType`` keys (preferred) or raw strings (forgiving
    for API-layer dict-from-JSON callers). Unknown string keys are dropped
    silently — the breakdown endpoint validates its inputs upstream.

    Phase 1 uses this for the read-only ``/admin/billing/breakdown`` panel.
    Phase 5 uses a different (prorated) computation against
    ``portal_user_seat_history``; see spec Section ``Seat-history table``.
    """
    table = SEAT_PRICE_YEARLY_MONTH_EQUIV if yearly_contract else SEAT_PRICE_MONTHLY
    total = 0
    for key, count in breakdown.items():
        seat = key if isinstance(key, SeatType) else _coerce_seat(key)
        if seat is None:
            continue
        total += table[seat] * int(count)
    return total


def _coerce_seat(value: str) -> SeatType | None:
    """Best-effort string -> SeatType, returning None for unknown values."""
    try:
        return SeatType(value)
    except ValueError:
        return None
