"""Per-user account-type model — SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0.

Klai's website (getklai.com/pricing) sells per-user pricing in TWO tiers:
``Klai Chat`` €28/user/mo and ``Klai Chat + Knowledge`` €68/user/mo.

The original SPEC (v0.1.0-v0.4.0) shipped a decoupled-axes model: ``role``
(permissions) and ``seat_type`` (billing) as independent admin-assigned
attributes. v0.5.0 (2026-05-13) collapses back to **role-derives-seat**:

  - The DB column is still ``portal_users.seat_type`` and the enum is
    still ``SeatType`` — renaming would force a cross-cutting migration.
  - The UX label is now "Account type" (en) / "Accounttype" (nl) — no
    "seat" jargon surfaces to admins.
  - Admin no longer chooses the account type; it is derived from the
    selected Profile via ``suggest_seat(role)``. The invite UI shows a
    read-only badge that auto-updates when Profile changes.
  - ``VIEWER`` tier is gone — getklai.com/pricing has only Klai Chat and
    Klai Chat + Knowledge.

Internal names (``seat_type``, ``SeatType``, etc.) stay so existing
Phase 1-4 column + migration + API surface keeps working unchanged.

@MX:ANCHOR fan_in=3+ -- canonical account-type catalogue. New tiers or
                        SEAT_FEATURES entries land here; downstream code
                        reads via these names and is NOT allowed to inline
                        a role -> SeatType dict literal (AC-13 ast-grep
                        rule enforces this).
"""

from __future__ import annotations

from enum import StrEnum

from app.core.features import FEATURE_MIN_PROFILE
from app.core.profiles import PROFILE_CAPABILITIES, PROFILE_RANK

# ---------------------------------------------------------------------------
# Account-type enum
# ---------------------------------------------------------------------------


class SeatType(StrEnum):
    """The two Klai account-type tiers.

    SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 (2026-05-13): ``VIEWER`` is
    gone. getklai.com/pricing offers only Klai Chat (€28) and Klai Chat
    + Knowledge (€68); a €0 viewer tier never existed in the product
    offer.
    """

    CHAT = "chat"
    KNOWLEDGE = "knowledge"


# ---------------------------------------------------------------------------
# Feature unlock per account type
# ---------------------------------------------------------------------------

# scribe + docs are INCLUDED in both paid tiers. The role-floor
# (``FEATURE_MIN_PROFILE`` in core/features.py: ``scribe -> company``,
# ``docs -> company``) keeps personal-role users out — they don't see
# scribe/docs in the sidebar even if their tier unlocks them.
SEAT_FEATURES: dict[SeatType, frozenset[str]] = {
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
            "templates.manage_org",
            "scribe",
            "docs",
        }
    ),
}


# Maps each capability string back to the account-type feature it requires.
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
    "templates.manage_org": "knowledge.full",
}


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

# Monthly rate per account type in EUR. Source of truth for Phase 5b
# Moneybird line-items; mirrors getklai.com/pricing.
SEAT_PRICE_MONTHLY: dict[SeatType, int] = {
    SeatType.CHAT: 28,
    SeatType.KNOWLEDGE: 68,
}

# Yearly-discount rate, expressed as the *equivalent per-month price* on
# an annual contract. €20/mo means €240/yr billed annually. €48/mo means
# €576/yr billed annually.
SEAT_PRICE_YEARLY_MONTH_EQUIV: dict[SeatType, int] = {
    SeatType.CHAT: 20,
    SeatType.KNOWLEDGE: 48,
}


# ---------------------------------------------------------------------------
# Profile-derives-account-type (v0.5.0: this is now strict, not "smart-default")
# ---------------------------------------------------------------------------

# v0.5.0: account type is derived from Profile, not admin-chosen. Personal /
# Company profiles (chat workloads) -> CHAT. KB-management profiles
# (kb_manager / group_manager / admin) -> KNOWLEDGE. The invite UI
# auto-updates the display when Profile changes; admin never picks the
# account type directly via the FE.
#
# (The PATCH /seat endpoint is still callable for admin-tooling
# escape-hatch but is no longer exposed in the UI.)
DEFAULT_SEAT_FOR_ROLE: dict[str, SeatType] = {
    "personal": SeatType.CHAT,
    "company": SeatType.CHAT,
    "kb_manager": SeatType.KNOWLEDGE,
    "group_manager": SeatType.KNOWLEDGE,
    "admin": SeatType.KNOWLEDGE,
}


def suggest_seat(role: str) -> SeatType:
    """Return the account type for a given Profile role.

    Pre-v0.5.0 this was the "smart-default" of a decoupled seat-selector;
    in v0.5.0 it is the canonical mapping (no admin override via the UI).
    Default for unknown roles is ``CHAT`` — the cheapest paid tier.

    This is the SINGLE allowed PROFILE -> SeatType mapping outside this
    module. AC-13 ast-grep blocks any other call site from inlining a
    role -> ``SeatType.<X>`` dict literal.
    """
    return DEFAULT_SEAT_FOR_ROLE.get(role, SeatType.CHAT)


# ---------------------------------------------------------------------------
# Effective access composition
# ---------------------------------------------------------------------------


def effective_features(seat_type: SeatType, role: str) -> frozenset[str]:
    """Return the set of product surfaces this (account-type, role) combo unlocks.

    Composition rule:
      1. Start with ``SEAT_FEATURES[seat_type]`` (the tier's unlocked set).
      2. For each feature, look up its role-floor in
         ``core/features.FEATURE_MIN_PROFILE`` (default ``personal``).
      3. Drop the feature if the caller's role-rank is below the floor.

    ``PROFILE_RANK.get(role, -1)`` defends against unknown role strings
    (e.g. a legacy snapshot value in ``portal_user_seat_history``).
    Unknown role -> rank -1 -> nothing unlocked. Fail-closed.
    """
    seat_unlocked = SEAT_FEATURES[seat_type]
    caller_rank = PROFILE_RANK.get(role, -1)
    return frozenset(
        feature
        for feature in seat_unlocked
        if caller_rank >= PROFILE_RANK.get(FEATURE_MIN_PROFILE.get(feature, "personal"), -1)
    )


def effective_capabilities(role: str, seat_type: SeatType) -> frozenset[str]:
    """Return the capabilities this (role, account-type) combo grants.

    Composition rule:
      1. Start with ``PROFILE_CAPABILITIES[role]`` (role-granted caps).
      2. For each capability, look up its required tier-feature in
         ``CAPABILITY_TO_SEAT_FEATURE``.
      3. Drop the capability if the tier does not unlock that feature.

    Examples (v0.5.0 — role-derives-tier means kb_manager always has
    KNOWLEDGE in production, but the (kb_manager, CHAT) cell stays a
    defined point in the matrix for admin-tooling correctness):
      - ``kb_manager`` + ``KNOWLEDGE`` -> full knowledge-tier capabilities
        (knowledge.full unlocks KB-management + org-template caps; both connector caps unlocked).
      - ``kb_manager`` + ``CHAT``      -> only ``kb.connectors`` (knowledge.full
        not unlocked by chat tier, and ``kb.connectors.external`` requires
        the external feature which chat-tier lacks). Reachable only via
        the API escape-hatch (PATCH /seat); the FE never produces this
        combo.

    Phase 4 swapped ``effective_kb_limits(role, plan).capabilities`` to
    intersect against this function instead of the plan-axis.
    """
    role_caps = PROFILE_CAPABILITIES.get(role, frozenset())
    seat_unlocked = SEAT_FEATURES[seat_type]
    return frozenset(cap for cap in role_caps if CAPABILITY_TO_SEAT_FEATURE.get(cap) in seat_unlocked)


def monthly_seat_cost(seat_type: SeatType, *, yearly_contract: bool = False) -> int:
    """Return the per-user monthly price in EUR.

    ``yearly_contract=True`` returns the *equivalent monthly* price under an
    annual contract (the yearly discount).
    """
    table = SEAT_PRICE_YEARLY_MONTH_EQUIV if yearly_contract else SEAT_PRICE_MONTHLY
    return table[seat_type]


def breakdown_to_monthly_bill(breakdown: dict[SeatType | str, int], *, yearly_contract: bool = False) -> int:
    """Sum a ``{seat_type: count}`` breakdown into a monthly total.

    Accepts either ``SeatType`` keys (preferred) or raw strings (forgiving
    for API-layer dict-from-JSON callers). Unknown string keys are dropped
    silently — the breakdown endpoint validates its inputs upstream.

    Phase 1 uses this for the read-only ``/admin/billing/breakdown`` panel.
    Phase 5b uses a different (prorated) computation against
    ``portal_user_seat_history``.
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
