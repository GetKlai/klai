"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 1 — unit tests for ``app.core.seats``.

Pure unit tests (no DB). Validates:

- ``SeatType`` enum has the three documented members.
- ``SEAT_FEATURES`` per-tier contents match the SPEC (Section "Two
  orthogonal user attributes / Attribute 1 -- seat_type").
- Every ``Capability`` member is either keyed in
  ``CAPABILITY_TO_SEAT_FEATURE`` OR appears in the test's explicit
  allow-list. This is the regression guard that catches a new capability
  landing without a corresponding seat-feature mapping (anti-pattern: a
  capability that grants nothing because no seat unlocks its feature).
- Pricing matches the marketing website.
- ``suggest_seat`` smart-default behavior incl. unknown-role fail-closed.
- ``effective_features`` composition with the role-floor in features.py
  (scribe/docs require ``company`` rank).
- ``effective_capabilities`` for the four canonical (role, seat) cells.
- ``breakdown_to_monthly_bill`` math + yearly-contract variant.
"""

from __future__ import annotations

import pytest

from app.core.profiles import Capability
from app.core.seats import (
    CAPABILITY_TO_SEAT_FEATURE,
    DEFAULT_SEAT_FOR_ROLE,
    SEAT_FEATURES,
    SEAT_PRICE_MONTHLY,
    SEAT_PRICE_YEARLY_MONTH_EQUIV,
    SeatType,
    breakdown_to_monthly_bill,
    effective_capabilities,
    effective_features,
    monthly_seat_cost,
    suggest_seat,
)

# ---------------------------------------------------------------------------
# Enum + tier contents
# ---------------------------------------------------------------------------


class TestSeatTypeEnum:
    def test_three_members(self) -> None:
        assert {s.value for s in SeatType} == {"viewer", "chat", "knowledge"}

    def test_str_value_equals_name_lower(self) -> None:
        # StrEnum: SeatType.CHAT == "chat" at runtime.
        assert SeatType.CHAT == "chat"
        assert SeatType.KNOWLEDGE == "knowledge"
        assert SeatType.VIEWER == "viewer"


class TestSeatFeaturesContents:
    def test_viewer_is_readonly_hints_only(self) -> None:
        assert SEAT_FEATURES[SeatType.VIEWER] == frozenset({"chat_readonly", "knowledge_readonly"})

    def test_chat_seat_unlocks_basic_kb_and_scribe_docs(self) -> None:
        feats = SEAT_FEATURES[SeatType.CHAT]
        assert "chat" in feats
        assert "knowledge.basic" in feats
        assert "kb.connectors" in feats
        assert "scribe" in feats
        assert "docs" in feats
        # Chat seat MUST NOT unlock knowledge.full or its KB-management
        # capabilities -- that's the whole point of the tier separation.
        assert "knowledge.full" not in feats
        assert "kb.create_org" not in feats
        assert "kb.connectors.external" not in feats

    def test_knowledge_seat_is_superset_of_chat_for_paid_surfaces(self) -> None:
        chat = SEAT_FEATURES[SeatType.CHAT]
        know = SEAT_FEATURES[SeatType.KNOWLEDGE]
        # Everything chat unlocks is also unlocked by knowledge.
        # Viewer's read-only hints are NOT in either paid tier on purpose.
        assert chat <= know
        # Plus the KB-management features.
        for extra in (
            "knowledge.full",
            "kb.connectors.external",
            "kb.create_org",
            "kb.members",
            "kb.taxonomy",
            "kb.gaps",
        ):
            assert extra in know, f"{extra!r} missing from KNOWLEDGE seat"


# ---------------------------------------------------------------------------
# Capability -> seat-feature mapping completeness
# ---------------------------------------------------------------------------


class TestCapabilityToSeatFeature:
    # Phase 1: every Capability that exists in core/profiles.py must be
    # represented here. If a future Capability lands without a mapping,
    # this test fails -- the implementer must explicitly opt out via the
    # allow-list below if the cap is intentionally outside the seat layer.
    _ALLOWED_TO_BE_UNMAPPED: frozenset[str] = frozenset()

    def test_every_capability_member_has_a_mapping(self) -> None:
        missing: list[str] = []
        for cap in Capability:
            if cap.value in self._ALLOWED_TO_BE_UNMAPPED:
                continue
            if cap.value not in CAPABILITY_TO_SEAT_FEATURE:
                missing.append(cap.value)
        assert missing == [], (
            f"Capability values without CAPABILITY_TO_SEAT_FEATURE entry: "
            f"{missing}. Add a mapping in seats.py OR add to the test "
            f"allow-list with rationale."
        )

    def test_mapped_features_exist_in_some_seat(self) -> None:
        # Every value in CAPABILITY_TO_SEAT_FEATURE must be unlockable by
        # at least one seat -- otherwise the capability is dead-mapped.
        all_unlocked = set().union(*SEAT_FEATURES.values())
        unreachable = sorted(v for v in set(CAPABILITY_TO_SEAT_FEATURE.values()) if v not in all_unlocked)
        assert unreachable == [], f"CAPABILITY_TO_SEAT_FEATURE values not in any SEAT_FEATURES set: {unreachable}"


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


class TestPricing:
    def test_monthly_prices_match_marketing_site(self) -> None:
        # getklai.com/pricing: Chat €28/mo, Chat+Knowledge €68/mo, Viewer €0.
        assert SEAT_PRICE_MONTHLY[SeatType.VIEWER] == 0
        assert SEAT_PRICE_MONTHLY[SeatType.CHAT] == 28
        assert SEAT_PRICE_MONTHLY[SeatType.KNOWLEDGE] == 68

    def test_yearly_month_equivalent_matches_marketing_site(self) -> None:
        # Annual contract: €20/mo Chat, €48/mo Chat+Knowledge.
        assert SEAT_PRICE_YEARLY_MONTH_EQUIV[SeatType.VIEWER] == 0
        assert SEAT_PRICE_YEARLY_MONTH_EQUIV[SeatType.CHAT] == 20
        assert SEAT_PRICE_YEARLY_MONTH_EQUIV[SeatType.KNOWLEDGE] == 48

    def test_monthly_seat_cost_helper_matches_table(self) -> None:
        for seat in SeatType:
            assert monthly_seat_cost(seat) == SEAT_PRICE_MONTHLY[seat]
            assert monthly_seat_cost(seat, yearly_contract=True) == SEAT_PRICE_YEARLY_MONTH_EQUIV[seat]


# ---------------------------------------------------------------------------
# suggest_seat -- the canonical PROFILE -> SeatType mapping
# ---------------------------------------------------------------------------


class TestSuggestSeat:
    @pytest.mark.parametrize(
        ("role", "expected"),
        [
            ("personal", SeatType.CHAT),
            ("company", SeatType.CHAT),
            ("kb_manager", SeatType.KNOWLEDGE),
            ("group_manager", SeatType.KNOWLEDGE),
            ("admin", SeatType.KNOWLEDGE),
        ],
    )
    def test_smart_default_per_role(self, role: str, expected: SeatType) -> None:
        assert suggest_seat(role) == expected

    def test_unknown_role_falls_back_to_chat_not_viewer(self) -> None:
        # Fail-closed pricing: an unknown role string MUST NOT produce a
        # free viewer seat. CHAT is the cheapest non-zero tier.
        assert suggest_seat("unknown_role_xyz") == SeatType.CHAT
        assert suggest_seat("") == SeatType.CHAT

    def test_default_seat_for_role_covers_every_profile_ladder_entry(self) -> None:
        from app.core.profiles import PROFILE_LADDER

        for role in PROFILE_LADDER:
            assert role in DEFAULT_SEAT_FOR_ROLE, f"PROFILE_LADDER entry {role!r} missing from DEFAULT_SEAT_FOR_ROLE"


# ---------------------------------------------------------------------------
# effective_features -- composition with FEATURE_MIN_PROFILE role-floor
# ---------------------------------------------------------------------------


class TestEffectiveFeatures:
    def test_personal_on_chat_seat_has_no_scribe_or_docs(self) -> None:
        # FEATURE_MIN_PROFILE: scribe -> company, docs -> company.
        # personal-role is rank 0; falls below floor.
        feats = effective_features(SeatType.CHAT, "personal")
        assert "chat" in feats
        assert "knowledge.basic" in feats
        assert "scribe" not in feats
        assert "docs" not in feats

    def test_company_on_chat_seat_sees_scribe_and_docs(self) -> None:
        feats = effective_features(SeatType.CHAT, "company")
        assert "scribe" in feats
        assert "docs" in feats

    def test_kb_manager_on_chat_seat_does_not_get_knowledge_full(self) -> None:
        feats = effective_features(SeatType.CHAT, "kb_manager")
        # Role is fine for scribe/docs floors, but chat seat does not
        # unlock knowledge.full at all.
        assert "knowledge.full" not in feats
        assert "kb.create_org" not in feats
        assert "scribe" in feats

    def test_admin_on_viewer_seat_sees_only_read_hints(self) -> None:
        feats = effective_features(SeatType.VIEWER, "admin")
        # Viewer is a billing-only seat. The FE-rendering hints survive
        # the role-floor (no floor entry for chat_readonly /
        # knowledge_readonly -> defaults to "personal" floor).
        assert feats == frozenset({"chat_readonly", "knowledge_readonly"})

    def test_unknown_role_fail_closed(self) -> None:
        # v0.4.0 hardening: an unknown role must not 500 the function.
        # PROFILE_RANK.get(role, -1) -> caller_rank = -1 -> nothing
        # clears the floor (even "personal" floor maps to rank 0).
        feats = effective_features(SeatType.KNOWLEDGE, "definitely-not-a-role")
        assert feats == frozenset()


# ---------------------------------------------------------------------------
# effective_capabilities -- the four canonical cells
# ---------------------------------------------------------------------------


class TestEffectiveCapabilities:
    def test_kb_manager_plus_knowledge_seat_gets_full_caps(self) -> None:
        caps = effective_capabilities("kb_manager", SeatType.KNOWLEDGE)
        # All six Capability members are unlocked: both connector caps via
        # their direct features, four KB-management caps via knowledge.full.
        assert caps == {
            Capability.KB_CONNECTORS,
            Capability.KB_CONNECTORS_EXTERNAL,
            Capability.KB_CREATE_ORG,
            Capability.KB_MEMBERS,
            Capability.KB_TAXONOMY,
            Capability.KB_GAPS,
        }

    def test_kb_manager_plus_chat_seat_keeps_only_basic_connector(self) -> None:
        caps = effective_capabilities("kb_manager", SeatType.CHAT)
        # Chat seat unlocks kb.connectors but NOT kb.connectors.external,
        # NOT knowledge.full -- so only the basic connector cap survives
        # the seat-filter.
        assert caps == {Capability.KB_CONNECTORS}

    def test_personal_plus_knowledge_seat_only_gets_what_role_grants(self) -> None:
        # personal has role caps = _KB_BASIC_CAPS = {kb.connectors}. The
        # knowledge seat unlocks plenty more features, but role-side caps
        # are the upper bound.
        caps = effective_capabilities("personal", SeatType.KNOWLEDGE)
        assert caps == {Capability.KB_CONNECTORS}

    def test_admin_plus_viewer_seat_returns_empty_set(self) -> None:
        # Admin retains role-rank powers via _require_at_least; capability
        # gating is independent and returns empty for viewer-seat.
        caps = effective_capabilities("admin", SeatType.VIEWER)
        assert caps == frozenset()

    def test_unknown_role_fail_closed(self) -> None:
        # PROFILE_CAPABILITIES.get(role, frozenset()) -> empty role caps
        # -> empty result. No KeyError.
        caps = effective_capabilities("definitely-not-a-role", SeatType.KNOWLEDGE)
        assert caps == frozenset()


# ---------------------------------------------------------------------------
# breakdown_to_monthly_bill -- the Phase 1 read-only billing helper
# ---------------------------------------------------------------------------


class TestBreakdownToMonthlyBill:
    def test_mixed_breakdown_sums_correctly(self) -> None:
        # 4 chat + 1 knowledge + 2 viewer = 4*28 + 1*68 + 2*0 = 180.
        result = breakdown_to_monthly_bill({SeatType.CHAT: 4, SeatType.KNOWLEDGE: 1, SeatType.VIEWER: 2})
        assert result == 4 * 28 + 1 * 68

    def test_yearly_contract_applies_discounted_rates(self) -> None:
        # Same headcount on yearly: 4*20 + 1*48 + 2*0 = 128.
        result = breakdown_to_monthly_bill(
            {SeatType.CHAT: 4, SeatType.KNOWLEDGE: 1, SeatType.VIEWER: 2},
            yearly_contract=True,
        )
        assert result == 4 * 20 + 1 * 48

    def test_accepts_string_keys_from_json_caller(self) -> None:
        result = breakdown_to_monthly_bill({"chat": 3, "knowledge": 2})
        assert result == 3 * 28 + 2 * 68

    def test_drops_unknown_seat_strings_silently(self) -> None:
        # API-layer validation must reject these; the helper is forgiving
        # so a future test that pipes a JSON object straight through does
        # not crash on a typo.
        result = breakdown_to_monthly_bill({"chat": 2, "premium_omg": 99, "knowledge": 1})
        assert result == 2 * 28 + 1 * 68

    def test_empty_breakdown_is_zero(self) -> None:
        assert breakdown_to_monthly_bill({}) == 0

    def test_all_viewer_returns_zero(self) -> None:
        assert breakdown_to_monthly_bill({SeatType.VIEWER: 10}) == 0
