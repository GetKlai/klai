"""Tests for ``app.services.imap_listener._process_email`` — SPEC-SEC-IMAP-001.

Covers the listener-level cross-cutting contracts:

- AC-7: ICS ``ORGANIZER`` mismatch emits ``imap_organizer_mismatch`` but is
  non-fatal — the listener proceeds using ``verified_from``.
- AC-10: ``find_tenant`` is NEVER called on unauthenticated mail; on accept
  it is called with ``verified_from`` (NOT the ICS organizer field).
- AC-11: ``imap_auth_failed`` and ``imap_auth_passed`` log entries contain
  exactly the REQ-4.1 top-level keys and no body/ICS payload.

Tests mock the IMAP client, ``find_tenant``, ``schedule_invite``, and
``parse_ics`` so the listener is exercised in isolation.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from app.services import imap_listener
from tests.services.fixtures.imap.builders import (
    build_email,
    dkim_sign,
    key_for,
    make_dnsfunc,
)


def _mock_imap(raw: bytes) -> MagicMock:
    """Minimal IMAP4_SSL mock: only ``fetch`` is called inside _process_email."""
    m = MagicMock()
    m.fetch = MagicMock(return_value=("OK", [(b"1 (RFC822)", raw)]))
    return m


def _fake_invite(organizer_email: str) -> SimpleNamespace:
    """Duck-typed Invite with just the attribute the listener reads."""
    return SimpleNamespace(organizer_email=organizer_email)


@pytest.fixture
def ics_part() -> bytes:
    """A byte string that parse_ics will see — we always mock parse_ics, so contents are irrelevant."""
    return b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"


@pytest.fixture
def patch_mailauth_dnsfunc(monkeypatch: pytest.MonkeyPatch):
    """Inject a test dnsfunc into verify_mail_auth so synthetic DKIM keys resolve."""
    from app.services import mail_auth

    original = mail_auth.verify_mail_auth

    async def _patched(raw_message: bytes, **kw):
        if "dnsfunc" not in kw or kw["dnsfunc"] is None:
            kw["dnsfunc"] = make_dnsfunc(
                key_for("customer.nl"),
                key_for("gmail.com"),
                key_for("accept-shape.test"),
            )
        return await original(raw_message, **kw)

    monkeypatch.setattr(imap_listener, "verify_mail_auth", _patched)


# ---------- AC-10 reject side: find_tenant never called -------------------


class TestAC10_RejectDoesNotCallFindTenant:
    """Every rejection scenario MUST NOT invoke find_tenant or schedule_invite."""

    @pytest.mark.asyncio
    async def test_forged_no_dkim_skips_tenant_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        find_tenant = AsyncMock()
        schedule_invite = AsyncMock()
        parse_ics = MagicMock()
        monkeypatch.setattr(imap_listener, "find_tenant", find_tenant)
        monkeypatch.setattr(imap_listener, "schedule_invite", schedule_invite)
        monkeypatch.setattr(imap_listener, "parse_ics", parse_ics)

        raw = build_email(from_addr="ceo@customer.nl")  # no DKIM
        await imap_listener._process_email(_mock_imap(raw), b"1")

        assert find_tenant.called is False
        assert schedule_invite.called is False
        assert parse_ics.called is False  # reject must land BEFORE parse_ics

    @pytest.mark.asyncio
    async def test_malformed_bytes_skips_tenant_lookup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        find_tenant = AsyncMock()
        schedule_invite = AsyncMock()
        monkeypatch.setattr(imap_listener, "find_tenant", find_tenant)
        monkeypatch.setattr(imap_listener, "schedule_invite", schedule_invite)

        raw = b"\x00\x01 not an email"
        await imap_listener._process_email(_mock_imap(raw), b"1")

        assert find_tenant.called is False
        assert schedule_invite.called is False


# ---------- AC-10 accept side: find_tenant called with verified_from ------


class TestAC10_AcceptCallsFindTenantWithVerifiedFrom:
    """On accept, find_tenant is called with verified_from — not the ICS organizer."""

    @pytest.mark.asyncio
    async def test_valid_dkim_uses_verified_from_for_tenant_lookup(
        self, monkeypatch: pytest.MonkeyPatch, patch_mailauth_dnsfunc, ics_part: bytes
    ) -> None:
        find_tenant = AsyncMock(return_value=("zitadel-user-123", 42))
        schedule_invite = AsyncMock()
        parse_ics = MagicMock(return_value=_fake_invite("attacker@evil.com"))
        monkeypatch.setattr(imap_listener, "find_tenant", find_tenant)
        monkeypatch.setattr(imap_listener, "schedule_invite", schedule_invite)
        monkeypatch.setattr(imap_listener, "parse_ics", parse_ics)
        monkeypatch.setattr(imap_listener, "_extract_ics_parts", lambda _msg: [ics_part])

        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")
        await imap_listener._process_email(_mock_imap(raw), b"1")

        # REQ-5.2: find_tenant called with verified From, never with ICS organizer.
        find_tenant.assert_awaited_once_with("boss@customer.nl")
        assert schedule_invite.await_count == 1


# ---------- AC-7: ICS organizer mismatch is warned, not fatal -------------


class TestAC7_OrganizerMismatchNonFatal:
    """Delegated-calendar case: ICS organizer differs from verified_from."""

    @pytest.mark.asyncio
    async def test_mismatch_emits_warning_and_proceeds(
        self, monkeypatch: pytest.MonkeyPatch, patch_mailauth_dnsfunc, ics_part: bytes
    ) -> None:
        find_tenant = AsyncMock(return_value=("uid", 1))
        schedule_invite = AsyncMock()
        parse_ics = MagicMock(return_value=_fake_invite("boss@customer.nl"))
        monkeypatch.setattr(imap_listener, "find_tenant", find_tenant)
        monkeypatch.setattr(imap_listener, "schedule_invite", schedule_invite)
        monkeypatch.setattr(imap_listener, "parse_ics", parse_ics)
        monkeypatch.setattr(imap_listener, "_extract_ics_parts", lambda _msg: [ics_part])

        raw = build_email(from_addr="pa@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        with capture_logs() as logs:
            await imap_listener._process_email(_mock_imap(raw), b"1")

        mismatch = [lg for lg in logs if lg["event"] == "imap_organizer_mismatch"]
        assert len(mismatch) == 1
        assert mismatch[0]["verified_from"] == "pa@customer.nl"
        assert mismatch[0]["ics_organizer"] == "boss@customer.nl"
        # REQ-5.3: proceed using verified_from despite the mismatch.
        find_tenant.assert_awaited_once_with("pa@customer.nl")
        assert schedule_invite.await_count == 1

    @pytest.mark.asyncio
    async def test_matching_organizer_does_not_warn(
        self, monkeypatch: pytest.MonkeyPatch, patch_mailauth_dnsfunc, ics_part: bytes
    ) -> None:
        find_tenant = AsyncMock(return_value=("uid", 1))
        schedule_invite = AsyncMock()
        parse_ics = MagicMock(return_value=_fake_invite("boss@customer.nl"))
        monkeypatch.setattr(imap_listener, "find_tenant", find_tenant)
        monkeypatch.setattr(imap_listener, "schedule_invite", schedule_invite)
        monkeypatch.setattr(imap_listener, "parse_ics", parse_ics)
        monkeypatch.setattr(imap_listener, "_extract_ics_parts", lambda _msg: [ics_part])

        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        with capture_logs() as logs:
            await imap_listener._process_email(_mock_imap(raw), b"1")

        mismatch = [lg for lg in logs if lg["event"] == "imap_organizer_mismatch"]
        assert mismatch == []


# ---------- AC-11: log schema stability -----------------------------------


_REJECT_KEYS = {
    "event",
    "log_level",
    "reason",
    "from_header",
    "from_domain",
    "dkim_result",
    "spf_result",
    "arc_result",
    "message_id",
}
_ACCEPT_KEYS = {
    "event",
    "log_level",
    "verified_from",
    "from_domain",
    "dkim_result",
    "spf_result",
    "arc_result",
    "message_id",
}


class TestAC11_LogSchemaStability:
    """imap_auth_failed + imap_auth_passed carry exactly the REQ-4.1 fields.

    In particular: no body, no ICS payload, no attachment content. Agents
    alerting on VictoriaLogs depend on this schema being stable.
    """

    @pytest.mark.asyncio
    async def test_reject_log_has_exact_required_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(imap_listener, "find_tenant", AsyncMock())
        monkeypatch.setattr(imap_listener, "schedule_invite", AsyncMock())
        monkeypatch.setattr(imap_listener, "parse_ics", MagicMock())

        raw = build_email(from_addr="ceo@customer.nl")
        with capture_logs() as logs:
            await imap_listener._process_email(_mock_imap(raw), b"1")

        fails = [lg for lg in logs if lg["event"] == "imap_auth_failed"]
        assert len(fails) == 1
        entry = fails[0]
        # REQ-4.1: exact key set, no surprises.
        assert set(entry.keys()) == _REJECT_KEYS
        # REQ-4.2: no body, no ICS payload, no attachment.
        assert "body" not in entry
        assert "payload" not in entry
        assert "attachment" not in entry
        assert "ics" not in entry

    @pytest.mark.asyncio
    async def test_accept_log_has_exact_required_keys(
        self, monkeypatch: pytest.MonkeyPatch, patch_mailauth_dnsfunc, ics_part: bytes
    ) -> None:
        monkeypatch.setattr(imap_listener, "find_tenant", AsyncMock(return_value=None))
        monkeypatch.setattr(imap_listener, "schedule_invite", AsyncMock())
        monkeypatch.setattr(imap_listener, "parse_ics", MagicMock(return_value=_fake_invite("boss@customer.nl")))
        monkeypatch.setattr(imap_listener, "_extract_ics_parts", lambda _msg: [ics_part])

        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")
        with capture_logs() as logs:
            await imap_listener._process_email(_mock_imap(raw), b"1")

        passes = [lg for lg in logs if lg["event"] == "imap_auth_passed"]
        assert len(passes) == 1
        entry = passes[0]
        assert set(entry.keys()) == _ACCEPT_KEYS
        assert "body" not in entry
        assert "payload" not in entry
        assert "attachment" not in entry

    @pytest.mark.asyncio
    async def test_no_op_on_no_ics_content_after_accept(
        self, monkeypatch: pytest.MonkeyPatch, patch_mailauth_dnsfunc
    ) -> None:
        """AC-4 edge case: Gmail passes auth but message has no ICS → silent skip.

        No ``imap_auth_failed`` because auth passed; find_tenant never called
        because there is nothing to schedule.
        """
        find_tenant = AsyncMock()
        monkeypatch.setattr(imap_listener, "find_tenant", find_tenant)
        monkeypatch.setattr(imap_listener, "schedule_invite", AsyncMock())
        monkeypatch.setattr(imap_listener, "_extract_ics_parts", lambda _msg: [])

        raw = build_email(from_addr="someone@gmail.com")
        raw = dkim_sign(raw, signing_domain="gmail.com")
        with capture_logs() as logs:
            await imap_listener._process_email(_mock_imap(raw), b"1")

        assert any(lg["event"] == "imap_auth_passed" for lg in logs)
        assert not any(lg["event"] == "imap_auth_failed" for lg in logs)
        assert find_tenant.called is False
