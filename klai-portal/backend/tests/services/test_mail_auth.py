"""Tests for ``app.services.mail_auth`` — SPEC-SEC-IMAP-001.

Covers AC-1..AC-9 from ``.moai/specs/SPEC-SEC-IMAP-001/acceptance.md``.
AC-7, AC-10, AC-11 are exercised at the listener layer (``test_imap_listener``).

Strategy:
- Integration tests (real dkimpy crypto, mocked DNS) for AC-1, AC-2, AC-3,
  AC-4, AC-9 — these exercise the full verify path including the library.
- Unit tests (mocked dkim.DKIM / dkim.ARC) for AC-5, AC-6, AC-8 — ARC-signed
  fixtures are fragile to build, and the wrapper logic (allowlist check,
  timeout handling, result assembly) is what this SPEC actually introduces.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import patch

import dkim
import pytest

from app.services.mail_auth import (
    MailAuthResult,
    _aligned,
    _organizational_domain,
    _outermost_arc_sealer,
    verify_mail_auth,
)
from tests.services.fixtures.imap.builders import (
    arc_sign,
    build_email,
    dkim_sign,
    key_for,
    make_dnsfunc,
)

# ---------- pure helpers ---------------------------------------------------


class TestAlignmentHelpers:
    """RFC 7489 §3.1.1 alignment — covered transitively by AC-2 / AC-3."""

    @pytest.mark.parametrize(
        ("a", "b", "aligned"),
        [
            ("customer.nl", "customer.nl", True),
            ("mail.customer.nl", "customer.nl", True),  # org-domain match
            ("customer.nl", "mail.customer.nl", True),
            ("CUSTOMER.NL", "customer.nl", True),  # case-insensitive
            ("spammer.net", "customer.nl", False),
            ("customer.com", "customer.nl", False),
            ("", "customer.nl", False),
            ("customer.nl", "", False),
        ],
    )
    def test_aligned_truth_table(self, a: str, b: str, aligned: bool) -> None:
        assert _aligned(a, b) is aligned

    @pytest.mark.parametrize(
        ("domain", "org"),
        [
            ("customer.nl", "customer.nl"),
            ("mail.customer.nl", "customer.nl"),
            ("deep.sub.customer.nl", "customer.nl"),
        ],
    )
    def test_organizational_domain(self, domain: str, org: str) -> None:
        assert _organizational_domain(domain) == org

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            # Public-suffix safety: two different SLDs under `.co.uk` MUST
            # NOT align. A naive two-label heuristic would reduce both to
            # `co.uk` and falsely accept the attacker's message.
            ("evil.co.uk", "target.co.uk"),
            ("sub.evil.co.uk", "target.co.uk"),
            # Same story for `.com.au`, another multi-label public suffix.
            ("evil.com.au", "target.com.au"),
        ],
    )
    def test_distinct_slds_under_public_suffix_do_not_align(self, a: str, b: str) -> None:
        assert _aligned(a, b) is False


class TestSettingsImapAuthservIdValidator:
    """SPEC-SEC-IMAP-001: the Settings model_validator that catches empty
    ``imap_authserv_id`` when the IMAP listener is enabled.

    Lives here (not in a config-specific suite) because the validator is
    SPEC-SEC-IMAP-001 scope and the doc-string references this file.
    """

    def test_listener_disabled_does_not_require_authserv_id(self) -> None:
        from app.core.config import Settings

        # No imap_host / imap_username → listener inactive → validator skipped.
        s = Settings(imap_host=None, imap_username=None, imap_authserv_id="")
        assert s.imap_host is None

    def test_listener_enabled_with_empty_authserv_id_fails_loud(self) -> None:
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="PORTAL_API_IMAP_AUTHSERV_ID"):
            Settings(
                imap_host="imap.example.com",
                imap_username="meet@example.com",
                imap_password="secret",
                imap_authserv_id="",
            )

    def test_listener_enabled_with_whitespace_authserv_id_fails_loud(self) -> None:
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="PORTAL_API_IMAP_AUTHSERV_ID"):
            Settings(
                imap_host="imap.example.com",
                imap_username="meet@example.com",
                imap_password="secret",
                imap_authserv_id="   ",
            )

    def test_listener_enabled_with_authserv_id_succeeds(self) -> None:
        from app.core.config import Settings

        s = Settings(
            imap_host="imap.example.com",
            imap_username="meet@example.com",
            imap_password="secret",
            imap_authserv_id="my-relay.example",
        )
        assert s.imap_authserv_id == "my-relay.example"


class TestArcSealerExtraction:
    """Regression for an April 2026 production incident: ``dkim.ARC.verify()``
    does NOT populate ``ARC.domain`` for verification flows; the sealing
    domain is in ``results[*]['as-domain']``. The original implementation
    read ``a.domain``, which silently returned ``sealer=None`` for every
    legitimately-forwarded invite — every real customer message rejected.
    """

    def test_extracts_outermost_seal_domain_from_results(self) -> None:
        results = [
            {"instance": 1, "as-domain": b"google.com"},
            {"instance": 2, "as-domain": b"getklai.com"},  # outermost
        ]
        assert _outermost_arc_sealer(results) == "getklai.com"

    def test_lowercases_domain(self) -> None:
        assert _outermost_arc_sealer([{"instance": 1, "as-domain": b"Google.COM"}]) == "google.com"

    def test_accepts_str_value(self) -> None:
        assert _outermost_arc_sealer([{"instance": 1, "as-domain": "fastmail.com"}]) == "fastmail.com"

    def test_returns_none_on_missing_or_empty(self) -> None:
        assert _outermost_arc_sealer(None) is None
        assert _outermost_arc_sealer([]) is None
        assert _outermost_arc_sealer([{"instance": 1}]) is None  # missing as-domain


# ---------- AC-1: forged From, no DKIM -------------------------------------


class TestAC1_NoDkim:
    """Forged From with no DKIM header rejects before any ICS parsing."""

    @pytest.mark.asyncio
    async def test_forged_no_dkim_rejects(self) -> None:
        raw = build_email(from_addr="ceo@customer.nl")

        result = await verify_mail_auth(
            raw,
            dnsfunc=make_dnsfunc(),
            timeout_seconds=5.0,
        )

        assert result.verified_from is None
        assert result.reason == "no_dkim_signature"
        assert result.from_header.strip() == "ceo@customer.nl"
        assert result.from_domain == "customer.nl"
        assert result.dkim_result.present is False
        assert result.arc_result.present is False


# ---------- AC-2: DKIM valid but misaligned -------------------------------


class TestAC2_DkimMisaligned:
    """Valid DKIM signature for ``d=spammer.net`` with ``From: @customer.nl``."""

    @pytest.mark.asyncio
    async def test_dkim_valid_misaligned_rejects(self) -> None:
        k = key_for("spammer.net")
        raw = build_email(from_addr="ceo@customer.nl")
        raw = dkim_sign(raw, signing_domain="spammer.net")

        result = await verify_mail_auth(
            raw,
            dnsfunc=make_dnsfunc(k),
            timeout_seconds=5.0,
        )

        assert result.verified_from is None
        assert result.reason == "dkim_misaligned"
        assert result.dkim_result.valid is True
        assert result.dkim_result.d == "spammer.net"
        assert result.dkim_result.aligned is False
        assert result.from_domain == "customer.nl"


# ---------- AC-3 + AC-4: Valid DKIM aligned -------------------------------


class TestAC3AC4_DkimValidAligned:
    """DKIM aligned to From — accept path. Covers both corporate and Gmail cases."""

    @pytest.mark.asyncio
    async def test_dkim_valid_aligned_corporate(self) -> None:
        k = key_for("customer.nl")
        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        result = await verify_mail_auth(
            raw,
            dnsfunc=make_dnsfunc(k),
            timeout_seconds=5.0,
        )

        assert result.verified_from == "boss@customer.nl"
        assert result.reason == ""
        assert result.dkim_result.valid is True
        assert result.dkim_result.aligned is True
        assert result.from_domain == "customer.nl"

    @pytest.mark.asyncio
    async def test_dkim_valid_aligned_gmail(self) -> None:
        k = key_for("gmail.com")
        raw = build_email(from_addr="someone@gmail.com")
        raw = dkim_sign(raw, signing_domain="gmail.com")

        result = await verify_mail_auth(
            raw,
            dnsfunc=make_dnsfunc(k),
            timeout_seconds=5.0,
        )

        assert result.verified_from == "someone@gmail.com"
        assert result.reason == ""
        assert result.dkim_result.valid is True
        assert result.dkim_result.aligned is True

    @pytest.mark.asyncio
    async def test_dkim_valid_org_domain_alignment(self) -> None:
        """DKIM d=customer.nl signing a From=user@mail.customer.nl is aligned."""
        k = key_for("customer.nl")
        raw = build_email(from_addr="user@mail.customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        result = await verify_mail_auth(
            raw,
            dnsfunc=make_dnsfunc(k),
            timeout_seconds=5.0,
        )

        assert result.verified_from == "user@mail.customer.nl"
        assert result.dkim_result.aligned is True


# ---------- AC-5 (real crypto): integration test for the ARC accept path -


class TestAC5_RealArcCrypto:
    """End-to-end ARC validation with no mocks — exercises ``dkim.arc_sign``
    plus ``dkim.arc_verify`` and the real ``_outermost_arc_sealer`` extraction.

    Catches the April 2026 production regression where mocked tests agreed
    with a wrong assumption about ``dkim.ARC.verify()``'s API surface
    (sealer was being read from ``ARC.domain`` instead of from the results
    list, returning ``None`` for every legitimately forwarded invite).
    """

    @pytest.mark.asyncio
    async def test_real_arc_chain_validates_and_accepts(self) -> None:
        sealer_key = key_for("getklai.com")
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[
                # Upstream MX authenticated and stamped — arc_sign will
                # copy this into the ARC-Authentication-Results header.
                (
                    "Authentication-Results",
                    "upstream-relay.test; dkim=pass header.d=customer.nl; spf=pass smtp.mailfrom=boss@customer.nl",
                ),
            ],
        )
        # No DKIM-Signature on the raw bytes → force ARC-only acceptance,
        # which is the production hot path (cloud86 strips DKIM on forward).
        signed = arc_sign(raw, sealer_domain="getklai.com", authserv_id="upstream-relay.test")

        result = await verify_mail_auth(
            signed,
            dnsfunc=make_dnsfunc(sealer_key),
            trusted_arc_sealers=["getklai.com"],
            authserv_id="upstream-relay.test",
        )

        assert result.verified_from == "boss@customer.nl"
        assert result.reason == ""
        assert result.arc_result.present is True
        assert result.arc_result.valid is True
        # The bug-bearing line: this assertion fails with sealer=None when
        # the wrapper reads from ``ARC.domain`` instead of results list.
        assert result.arc_result.sealer == "getklai.com"
        assert result.arc_result.trusted is True
        assert result.arc_result.aligned_from_domain is True

    @pytest.mark.asyncio
    async def test_real_arc_untrusted_sealer_rejects(self) -> None:
        sealer_key = key_for("weird-provider.example")
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[
                (
                    "Authentication-Results",
                    "upstream-relay.test; dkim=pass header.d=customer.nl",
                ),
            ],
        )
        signed = arc_sign(raw, sealer_domain="weird-provider.example", authserv_id="upstream-relay.test")

        result = await verify_mail_auth(
            signed,
            dnsfunc=make_dnsfunc(sealer_key),
            trusted_arc_sealers=["getklai.com", "google.com"],  # weird-provider NOT in list
            authserv_id="upstream-relay.test",
        )

        assert result.verified_from is None
        assert result.reason == "arc_untrusted_sealer"
        assert result.arc_result.sealer == "weird-provider.example"
        assert result.arc_result.trusted is False


# ---------- AC-5: Valid ARC from trusted sealer (forwarded) ---------------


class TestAC5_ArcTrustedForwarded:
    """Valid ARC chain from allowlisted sealer with inner DKIM aligned."""

    @pytest.mark.asyncio
    async def test_arc_trusted_with_inner_alignment_accepts(self) -> None:
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[
                (
                    "ARC-Seal",
                    "i=1; a=rsa-sha256; cv=none; d=google.com; s=test; t=1; b=XX",
                ),
                (
                    "ARC-Message-Signature",
                    "i=1; a=rsa-sha256; c=relaxed/relaxed; d=google.com; s=test; h=from:to; bh=XX; b=XX",
                ),
                (
                    "ARC-Authentication-Results",
                    "i=1; mx.google.com; dkim=pass header.d=customer.nl; spf=pass smtp.mailfrom=boss@customer.nl",
                ),
            ],
        )

        # Mock both dkim.DKIM and dkim.ARC so we exercise the wrapper logic
        # without building a cryptographically valid ARC chain (fragile).
        with (
            patch("app.services.mail_auth.dkim.DKIM") as mock_dkim_cls,
            patch("app.services.mail_auth.dkim.ARC") as mock_arc_cls,
        ):
            mock_dkim = mock_dkim_cls.return_value
            mock_dkim.verify.side_effect = dkim.DKIMException("broken signature")
            mock_dkim.domain = None

            mock_arc = mock_arc_cls.return_value
            # dkim.ARC.verify returns (cv, results, reason); the sealing
            # domain is in results[*]['as-domain'] (NOT in `a.domain`).
            mock_arc.verify.return_value = (
                dkim.CV_Pass,
                [{"instance": 1, "as-domain": b"google.com"}],
                "",
            )

            result = await verify_mail_auth(
                raw,
                trusted_arc_sealers=["google.com", "outlook.com"],
                timeout_seconds=5.0,
            )

        assert result.verified_from == "boss@customer.nl"
        assert result.reason == ""
        assert result.arc_result.valid is True
        assert result.arc_result.sealer == "google.com"
        assert result.arc_result.trusted is True
        assert result.arc_result.aligned_from_domain is True


# ---------- AC-6: ARC from untrusted sealer -------------------------------


class TestAC6_ArcUntrustedSealer:
    """Valid ARC chain, but the sealing domain is not in the allowlist."""

    @pytest.mark.asyncio
    async def test_arc_untrusted_sealer_rejects(self) -> None:
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[
                (
                    "ARC-Seal",
                    "i=1; a=rsa-sha256; cv=none; d=weird-provider.example; s=test; t=1; b=XX",
                ),
                (
                    "ARC-Authentication-Results",
                    "i=1; weird-provider.example; dkim=pass header.d=customer.nl",
                ),
            ],
        )

        with (
            patch("app.services.mail_auth.dkim.DKIM") as mock_dkim_cls,
            patch("app.services.mail_auth.dkim.ARC") as mock_arc_cls,
        ):
            mock_dkim = mock_dkim_cls.return_value
            mock_dkim.verify.side_effect = dkim.DKIMException("broken")
            mock_dkim.domain = None

            mock_arc = mock_arc_cls.return_value
            mock_arc.verify.return_value = (
                dkim.CV_Pass,
                [{"instance": 1, "as-domain": b"weird-provider.example"}],
                "",
            )

            result = await verify_mail_auth(
                raw,
                trusted_arc_sealers=["google.com", "outlook.com"],
                timeout_seconds=5.0,
            )

        assert result.verified_from is None
        assert result.reason == "arc_untrusted_sealer"
        assert result.arc_result.valid is True
        assert result.arc_result.sealer == "weird-provider.example"
        assert result.arc_result.trusted is False


# ---------- AC-8: Verification timeout ------------------------------------


class TestAC8_Timeout:
    """DKIM verify hanging past the 5s ceiling → dkim_timeout reject."""

    @pytest.mark.asyncio
    async def test_dkim_timeout_rejects(self) -> None:
        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        async def _slow_verify(*_args: Any, **_kwargs: Any) -> Any:
            await asyncio.sleep(10)  # would hit the 0.1s ceiling below
            return {}, {}

        with patch("app.services.mail_auth.asyncio.to_thread", side_effect=_slow_verify):
            result = await verify_mail_auth(
                raw,
                timeout_seconds=0.1,  # tight ceiling for test speed
            )

        assert result.verified_from is None
        assert result.reason == "dkim_timeout"
        assert result.from_domain == "customer.nl"


# ---------- AC-9: Malformed headers fail closed ---------------------------


class TestAC9_Malformed:
    """Any unhandled exception in verify → reject with malformed_headers."""

    @pytest.mark.asyncio
    async def test_verify_crashes_rejects_fail_closed(self) -> None:
        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        def _exploding_verify(*_args: Any, **_kwargs: Any) -> Any:
            raise ValueError("simulated unexpected crash in dkimpy")

        with patch("app.services.mail_auth._verify_crypto_sync", side_effect=_exploding_verify):
            result = await verify_mail_auth(raw, timeout_seconds=5.0)

        assert result.verified_from is None
        assert result.reason == "malformed_headers"
        # SPEC REQ-9: reject-on-exception path still produces structured result
        assert isinstance(result, MailAuthResult)

    @pytest.mark.asyncio
    async def test_bytes_not_an_email_rejects(self) -> None:
        result = await verify_mail_auth(b"\x00\x01\x02 not an email at all")

        assert result.verified_from is None
        assert result.reason == "malformed_headers"

    @pytest.mark.asyncio
    async def test_empty_from_header_rejects(self) -> None:
        raw = build_email()
        # Replace the From header with a malformed value
        raw = raw.replace(b"From: sender@example.com", b"From: (no address here)")
        result = await verify_mail_auth(raw)

        assert result.verified_from is None
        assert result.reason == "malformed_headers"


# ---------- AC-11: log schema stability (structured result contract) ------


class TestAC11_ResultSchema:
    """Every MailAuthResult carries the same typed sub-results regardless of verdict.

    The :class:`DkimResult` / :class:`SpfResult` / :class:`ArcResult` frozen
    dataclasses replace the previous ``dict[str, Any]`` shape, so a typo in
    a field access is now a pyright / runtime error rather than a silent
    KeyError-or-None in prod.
    """

    @pytest.mark.asyncio
    async def test_reject_result_has_stable_shape(self) -> None:
        raw = build_email(from_addr="ceo@customer.nl")
        result = await verify_mail_auth(raw)

        assert {f.name for f in dataclasses.fields(result.dkim_result)} == {
            "present",
            "valid",
            "d",
            "aligned",
        }
        assert {f.name for f in dataclasses.fields(result.spf_result)} == {
            "result",
            "smtp_mailfrom_domain",
            "aligned",
        }
        assert {f.name for f in dataclasses.fields(result.arc_result)} == {
            "present",
            "valid",
            "sealer",
            "trusted",
            "aligned_from_domain",
        }
        # REQ-4.1 reject reason enum — the RejectReason Literal in
        # app.services.mail_auth defines the exact set; this is a
        # runtime cross-check against the subset `_verdict` emits.
        assert result.reason in {
            "no_dkim_signature",
            "dkim_invalid",
            "dkim_misaligned",
            "arc_invalid",
            "arc_untrusted_sealer",
            "no_auth_signal",
            "dkim_timeout",
            "malformed_headers",
            "",
        }

    @pytest.mark.asyncio
    async def test_accept_result_has_same_shape(self) -> None:
        k = key_for("accept-shape.test")
        raw = build_email(from_addr="user@accept-shape.test")
        raw = dkim_sign(raw, signing_domain="accept-shape.test")
        result = await verify_mail_auth(raw, dnsfunc=make_dnsfunc(k))

        assert result.verified_from == "user@accept-shape.test"
        assert result.reason == ""
        assert result.parsed_message is not None
        assert result.message_id  # non-empty; either real or "<unknown>"


# ---------- REQ-2.1: SPF alignment pulled from trusted Auth-Results --------


class TestSPFFromAuthResults:
    """REQ-2.4 trust boundary: only the configured authserv-id is consulted."""

    @pytest.mark.asyncio
    async def test_spf_pass_aligned_flows_into_result(self) -> None:
        k = key_for("spf-aligned.test")
        raw = build_email(
            from_addr="boss@spf-aligned.test",
            extra_headers=[
                (
                    "Authentication-Results",
                    "mail.getklai.com; spf=pass smtp.mailfrom=boss@spf-aligned.test; dkim=pass header.d=spf-aligned.test",
                )
            ],
        )
        raw = dkim_sign(raw, signing_domain="spf-aligned.test")

        result = await verify_mail_auth(raw, dnsfunc=make_dnsfunc(k), authserv_id="mail.getklai.com")

        assert result.verified_from == "boss@spf-aligned.test"
        assert result.spf_result.result == "pass"
        assert result.spf_result.smtp_mailfrom_domain == "spf-aligned.test"
        assert result.spf_result.aligned is True

    @pytest.mark.asyncio
    async def test_spf_misaligned_not_marked_aligned(self) -> None:
        # Two distinct registerable domains (different SLDs under the PSL).
        k = key_for("dkim-only.com")
        raw = build_email(
            from_addr="boss@dkim-only.com",
            extra_headers=[
                (
                    "Authentication-Results",
                    "mail.getklai.com; spf=pass smtp.mailfrom=forwarder@mail-relay.com; dkim=pass header.d=dkim-only.com",
                )
            ],
        )
        raw = dkim_sign(raw, signing_domain="dkim-only.com")

        result = await verify_mail_auth(raw, dnsfunc=make_dnsfunc(k), authserv_id="mail.getklai.com")

        # DKIM-aligned accept still happens; SPF is a soft signal per REQ-2.2.
        assert result.verified_from == "boss@dkim-only.com"
        assert result.spf_result.result == "pass"
        assert result.spf_result.aligned is False
        assert result.spf_result.smtp_mailfrom_domain == "mail-relay.com"

    @pytest.mark.asyncio
    async def test_authserv_id_filter_ignores_sender_injected_auth_results(self) -> None:
        """Attacker forges an Authentication-Results header at a different authserv-id.

        Security-critical: must NOT be consulted. We consult ONLY the header
        stamped by the configured trusted relay (``mail.getklai.com``).
        """
        k = key_for("dkim-only-again.test")
        raw = build_email(
            from_addr="boss@dkim-only-again.test",
            extra_headers=[
                # Sender-injected lie — must be ignored.
                (
                    "Authentication-Results",
                    "attacker.example; spf=pass smtp.mailfrom=attacker@evil.test",
                )
            ],
        )
        raw = dkim_sign(raw, signing_domain="dkim-only-again.test")

        result = await verify_mail_auth(raw, dnsfunc=make_dnsfunc(k), authserv_id="mail.getklai.com")

        # DKIM alignment still carries the accept; SPF signal from untrusted
        # header is discarded (result stays "absent").
        assert result.verified_from == "boss@dkim-only-again.test"
        assert result.spf_result.result == "absent"
        assert result.spf_result.aligned is False


# ---------- REQ-3: ARC edge cases -----------------------------------------


class TestARCEdgeCases:
    """Malformed ARC-Authentication-Results + no-ARC variants."""

    @pytest.mark.asyncio
    async def test_malformed_arc_auth_results_does_not_align(self) -> None:
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[
                (
                    "ARC-Seal",
                    "i=1; a=rsa-sha256; cv=none; d=google.com; s=test; t=1; b=XX",
                ),
                # No i= prefix → defensive parse skips it.
                (
                    "ARC-Authentication-Results",
                    "no-prefix-here; dkim=pass header.d=customer.nl",
                ),
                # Garbage → parse raises → defensive skip.
                ("ARC-Authentication-Results", "i=notanumber; broken"),
            ],
        )

        with (
            patch("app.services.mail_auth.dkim.DKIM") as mock_dkim_cls,
            patch("app.services.mail_auth.dkim.ARC") as mock_arc_cls,
        ):
            mock_dkim_cls.return_value.verify.side_effect = dkim.DKIMException("broken")
            mock_dkim_cls.return_value.domain = None
            mock_arc_cls.return_value.verify.return_value = (
                dkim.CV_Pass,
                [{"instance": 1, "as-domain": b"google.com"}],
                "",
            )

            result = await verify_mail_auth(raw, trusted_arc_sealers=["google.com"], timeout_seconds=5.0)

        # Both ARC-AR headers unparseable → aligned_from_domain stays False.
        # Sealer IS trusted but alignment failed; DKIM is absent; no positive
        # signal remains → no_auth_signal.
        assert result.verified_from is None
        assert result.reason == "no_auth_signal"
        assert result.arc_result.aligned_from_domain is False
        assert result.arc_result.trusted is True

    @pytest.mark.asyncio
    async def test_arc_inner_dkim_fail_does_not_align(self) -> None:
        """ARC-AR with dkim=fail (not pass) must not satisfy alignment."""
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[
                ("ARC-Seal", "i=1; a=rsa-sha256; cv=none; d=google.com; s=test; t=1; b=XX"),
                (
                    "ARC-Authentication-Results",
                    "i=1; mx.google.com; dkim=fail header.d=customer.nl",
                ),
            ],
        )

        with (
            patch("app.services.mail_auth.dkim.DKIM") as mock_dkim_cls,
            patch("app.services.mail_auth.dkim.ARC") as mock_arc_cls,
        ):
            mock_dkim_cls.return_value.verify.side_effect = dkim.DKIMException("broken")
            mock_dkim_cls.return_value.domain = None
            mock_arc_cls.return_value.verify.return_value = (
                dkim.CV_Pass,
                [{"instance": 1, "as-domain": b"google.com"}],
                "",
            )

            result = await verify_mail_auth(raw, trusted_arc_sealers=["google.com"], timeout_seconds=5.0)

        assert result.verified_from is None
        assert result.arc_result.aligned_from_domain is False

    @pytest.mark.asyncio
    async def test_arc_cv_fail_yields_arc_invalid(self) -> None:
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[
                ("ARC-Seal", "i=1; a=rsa-sha256; cv=none; d=google.com; s=test; t=1; b=XX"),
            ],
        )

        with (
            patch("app.services.mail_auth.dkim.DKIM") as mock_dkim_cls,
            patch("app.services.mail_auth.dkim.ARC") as mock_arc_cls,
        ):
            mock_dkim_cls.return_value.verify.side_effect = dkim.DKIMException("broken")
            mock_dkim_cls.return_value.domain = None
            mock_arc_cls.return_value.verify.return_value = (
                dkim.CV_Fail,
                [{"instance": 1, "as-domain": b"google.com"}],
                "",
            )

            result = await verify_mail_auth(raw, trusted_arc_sealers=["google.com"], timeout_seconds=5.0)

        assert result.verified_from is None
        assert result.reason == "arc_invalid"

    @pytest.mark.asyncio
    async def test_arc_raises_yields_arc_invalid(self) -> None:
        """dkim.ARC.verify raising DKIMException maps to arc_invalid, not crash."""
        raw = build_email(
            from_addr="boss@customer.nl",
            extra_headers=[("ARC-Seal", "i=1; d=google.com; s=test; t=1; b=XX")],
        )

        with (
            patch("app.services.mail_auth.dkim.DKIM") as mock_dkim_cls,
            patch("app.services.mail_auth.dkim.ARC") as mock_arc_cls,
        ):
            mock_dkim_cls.return_value.verify.return_value = False
            mock_dkim_cls.return_value.domain = None
            mock_arc_cls.return_value.verify.side_effect = dkim.DKIMException("broken arc")
            mock_arc_cls.return_value.domain = None

            result = await verify_mail_auth(raw, trusted_arc_sealers=["google.com"], timeout_seconds=5.0)

        assert result.verified_from is None
        assert result.arc_result.valid is False
        assert result.arc_result.present is True


# ---------- REQ-1: DKIM exception + invalid sig variants ------------------


class TestDkimInvalidBranches:
    """dkimpy raises DKIMException or returns False — wrapper must classify correctly."""

    @pytest.mark.asyncio
    async def test_dkim_exception_yields_dkim_invalid(self) -> None:
        """A DKIMException from verify (e.g. bad key) → dkim_invalid reject."""
        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        # Use a dnsfunc that returns a WRONG key for customer.nl so crypto fails.
        def bad_dns(name, *_a, **_kw):
            return b"v=DKIM1; k=rsa; p=INVALID_KEY"

        result = await verify_mail_auth(raw, dnsfunc=bad_dns, timeout_seconds=5.0)

        assert result.verified_from is None
        assert result.reason == "dkim_invalid"
        assert result.dkim_result.present is True
        assert result.dkim_result.valid is False

    @pytest.mark.asyncio
    async def test_arc_with_dkim_invalid_and_no_arc_header_yields_dkim_invalid(
        self,
    ) -> None:
        """DKIM present but invalid; no ARC header → dkim_invalid (not no_dkim_signature)."""
        raw = build_email(from_addr="boss@customer.nl")
        raw = dkim_sign(raw, signing_domain="customer.nl")

        with patch("app.services.mail_auth.dkim.DKIM") as mock_dkim_cls:
            mock_dkim = mock_dkim_cls.return_value
            mock_dkim.verify.return_value = False
            mock_dkim.domain = None

            result = await verify_mail_auth(raw, timeout_seconds=5.0)

        assert result.verified_from is None
        assert result.reason == "dkim_invalid"
