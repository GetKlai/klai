"""Mail authentication helper for the IMAP calendar listener.

SPEC-SEC-IMAP-001. Verifies DKIM (aligned to RFC-5322 From domain), reads
SPF from the trusted upstream Authentication-Results header, and accepts a
valid ARC chain from a trusted sealer as a substitute for direct DKIM
alignment on forwarded mail. Returns a structured :class:`MailAuthResult`;
callers gate on ``verified_from is not None``.

Call site: :func:`app.services.imap_listener._process_email`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import email
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from email.utils import getaddresses
from typing import Any, Literal

import authres
import dkim
import publicsuffix2
import structlog

from app.core.config import settings

logger = structlog.get_logger()

DnsFunc = Callable[[str], bytes]

# REQ-4.1: every possible value of :attr:`MailAuthResult.reason`. Empty string
# means "pass". A ``Literal`` type means a typo here is caught by pyright, not
# by a missed test assertion.
RejectReason = Literal[
    "",
    "no_dkim_signature",
    "dkim_invalid",
    "dkim_misaligned",
    "arc_invalid",
    "arc_untrusted_sealer",
    "no_auth_signal",
    "dkim_timeout",
    "malformed_headers",
]


@dataclass(frozen=True)
class DkimResult:
    """REQ-1: verdict of the first DKIM-Signature header.

    ``present`` is True iff a DKIM-Signature header is in the message;
    ``valid`` is True iff crypto-verify passed; ``d`` is the signing domain
    from the verifying signature; ``aligned`` is True iff ``d`` aligns with
    the RFC-5322 From domain per RFC 7489 §3.1.1.
    """

    present: bool = False
    valid: bool = False
    d: str | None = None
    aligned: bool = False


@dataclass(frozen=True)
class SpfResult:
    """REQ-2: SPF verdict read from the trusted upstream Authentication-Results."""

    result: str = "absent"
    smtp_mailfrom_domain: str = ""
    aligned: bool = False


@dataclass(frozen=True)
class ArcResult:
    """REQ-3: verdict of the ARC chain, with sealer-allowlist resolution."""

    present: bool = False
    valid: bool = False
    sealer: str | None = None
    trusted: bool = False
    aligned_from_domain: bool = False


@dataclass(frozen=True)
class MailAuthResult:
    """REQ-1.5: structured mail-auth verdict.

    Invariants:

    - ``verified_from is not None`` iff the message passed
    - On pass, ``reason == ""``; on reject, ``reason`` is a REQ-4.1 code
    - ``parsed_message is not None`` iff the raw bytes were parseable as
      RFC-822 (so the accept path can always use it; the very rare
      ``malformed_headers`` branch from a raise inside ``message_from_bytes``
      is the only case where it is None)

    Downstream callers MUST gate on ``verified_from is not None``. The
    individual :class:`DkimResult` / :class:`SpfResult` / :class:`ArcResult`
    fields are for logging only; combining them outside this module is a
    footgun (e.g. DKIM valid-but-misaligned is NOT acceptance).
    """

    dkim_result: DkimResult
    spf_result: SpfResult
    arc_result: ArcResult
    from_header: str
    from_domain: str
    message_id: str
    verified_from: str | None
    reason: RejectReason
    parsed_message: Message | None = field(default=None, repr=False, compare=False)


def _organizational_domain(domain: str) -> str:
    """RFC 7489 §3.1.1 organizational domain via the Public Suffix List.

    A naive two-label heuristic misaligns on ccTLDs with multi-label public
    suffixes: ``evil.co.uk`` and ``target.co.uk`` both yield ``co.uk`` and
    therefore would "align". :func:`publicsuffix2.get_sld` consults the
    IANA-maintained PSL and returns the correct organizational domain
    (``evil.co.uk`` → ``evil.co.uk``, ``mail.customer.nl`` → ``customer.nl``).
    """
    normalized = domain.lower().strip(".")
    return publicsuffix2.get_sld(normalized) or normalized


def _aligned(signing_domain: str, from_domain: str) -> bool:
    """Exact OR organizational-domain alignment per RFC 7489 §3.1.1."""
    if not signing_domain or not from_domain:
        return False
    a, b = signing_domain.lower(), from_domain.lower()
    return a == b or _organizational_domain(a) == _organizational_domain(b)


def _extract_from(msg: Message) -> tuple[str, str, str | None]:
    """Return (raw_from_header, from_domain_lower, normalized_from_address).

    Normalized address is lowercased email only (no display name). All three
    values empty/None if the header is absent or unparseable.
    """
    raw = msg.get("From", "") or ""
    addrs = getaddresses([raw])
    if not addrs or not addrs[0][1] or "@" not in addrs[0][1]:
        return raw, "", None
    addr = addrs[0][1].lower()
    _, _, domain = addr.rpartition("@")
    return raw, domain, addr


def _dkim_verify_sync(raw: bytes, dnsfunc: DnsFunc | None, timeout: float, from_domain: str) -> DkimResult:
    """REQ-1.2: crypto-verify the first DKIM-Signature and check alignment.

    Alignment is folded into this function so the returned :class:`DkimResult`
    is complete — no post-hoc field mutation required at the call site.
    """
    if b"dkim-signature:" not in raw.lower():
        return DkimResult()
    d = dkim.DKIM(raw, timeout=int(timeout))
    try:
        valid = bool(d.verify(dnsfunc=dnsfunc) if dnsfunc is not None else d.verify())
    except dkim.DKIMException:
        return DkimResult(present=True)
    signing = d.domain.decode().lower() if d.domain else None
    return DkimResult(
        present=True,
        valid=valid,
        d=signing,
        aligned=valid and signing is not None and _aligned(signing, from_domain),
    )


def _arc_verify_sync(
    raw: bytes,
    msg: Message,
    dnsfunc: DnsFunc | None,
    timeout: float,
    trusted_sealers: set[str],
    from_domain: str,
) -> ArcResult:
    """REQ-3: verify ARC chain and check innermost Auth-Results for aligned DKIM."""
    if b"arc-seal:" not in raw.lower():
        return ArcResult()
    a = dkim.ARC(raw, timeout=int(timeout))
    try:
        cv, _results, _reason = a.verify(dnsfunc=dnsfunc) if dnsfunc is not None else a.verify()
    except dkim.DKIMException:
        return ArcResult(present=True)

    valid = cv == dkim.CV_Pass
    sealer = a.domain.decode().lower() if a.domain else None
    trusted = sealer in trusted_sealers if sealer else False
    aligned_from = valid and trusted and _arc_inner_dkim_aligned(msg, from_domain)
    return ArcResult(
        present=True,
        valid=valid,
        sealer=sealer,
        trusted=trusted,
        aligned_from_domain=aligned_from,
    )


def _arc_inner_dkim_aligned(msg: Message, from_domain: str) -> bool:
    """REQ-3.2: check the innermost ARC-Authentication-Results for aligned DKIM=pass.

    Innermost hop = lowest ``i=`` value in the chain. Per RFC 8617 §4.2, the
    ARC-Authentication-Results header is prefixed with ``i=N;`` followed by
    an ordinary Authentication-Results body.
    """
    parsed: list[tuple[int, Any]] = []
    for raw in msg.get_all("ARC-Authentication-Results", []) or []:
        try:
            prefix, _, rest = raw.partition(";")
            prefix = prefix.strip()
            if not prefix.startswith("i="):
                continue
            i_val = int(prefix[2:])
            ar = authres.parse("Authentication-Results: " + rest.strip())
            parsed.append((i_val, ar))
        except Exception:
            # Defensive: one malformed ARC hop MUST NOT void the whole chain.
            logger.debug("arc_auth_results_parse_failed", exc_info=True)
            continue
    if not parsed:
        return False
    parsed.sort(key=lambda x: x[0])
    inner = parsed[0][1]
    for r in inner.results:
        if r.method.lower() != "dkim":
            continue
        if r.result and r.result.lower() != "pass":
            continue
        header_d = next(
            (p.value for p in r.properties if p.type == "header" and p.name == "d"),
            "",
        )
        if _aligned(header_d, from_domain):
            return True
    return False


def _trusted_auth_results(msg: Message, authserv_id: str) -> list[Any]:
    """Return only Authentication-Results stamped by the configured authserv-id.

    REQ-2.4 trust boundary: a sender-injected Authentication-Results header is
    attacker-controlled and MUST be ignored.
    """
    out: list[Any] = []
    wanted = authserv_id.lower()
    for raw in msg.get_all("Authentication-Results", []) or []:
        try:
            ar = authres.parse("Authentication-Results: " + raw)
        except Exception:
            # One malformed Auth-Results header MUST NOT stop scanning the rest.
            logger.debug("auth_results_parse_failed", exc_info=True)
            continue
        if ar.authserv_id and ar.authserv_id.lower() == wanted:
            out.append(ar)
    return out


def _spf_from_auth_results(ars: list[Any], from_domain: str) -> SpfResult:
    """REQ-2.1: pull SPF verdict + alignment from trusted Authentication-Results."""
    for ar in ars:
        for r in ar.results:
            if r.method.lower() != "spf":
                continue
            result = (r.result or "").lower()
            mailfrom_domain = ""
            for p in r.properties:
                if p.type == "smtp" and p.name == "mailfrom":
                    _, _, mailfrom_domain = p.value.rpartition("@")
                    mailfrom_domain = mailfrom_domain.lower()
                    break
            return SpfResult(
                result=result or "absent",
                smtp_mailfrom_domain=mailfrom_domain,
                aligned=result == "pass" and _aligned(mailfrom_domain, from_domain),
            )
    return SpfResult()


def _verdict(dkim_r: DkimResult, arc_r: ArcResult) -> RejectReason:
    """Return empty string on accept, REQ-4.1 reason code on reject.

    Accept iff one of:

    - DKIM valid AND aligned to From domain (REQ-1.2)
    - ARC valid AND sealer trusted AND inner DKIM aligned to From (REQ-3.2)
    """
    if dkim_r.valid and dkim_r.aligned:
        return ""
    if arc_r.valid and arc_r.trusted and arc_r.aligned_from_domain:
        return ""

    # Ordered most-specific-first so log `reason` codes are actionable.
    if arc_r.present and arc_r.valid and not arc_r.trusted:
        return "arc_untrusted_sealer"
    if arc_r.present and not arc_r.valid:
        return "arc_invalid"
    if dkim_r.present and dkim_r.valid and not dkim_r.aligned:
        return "dkim_misaligned"
    if dkim_r.present and not dkim_r.valid:
        return "dkim_invalid"
    if not dkim_r.present and not arc_r.present:
        return "no_dkim_signature"
    return "no_auth_signal"


async def verify_mail_auth(
    raw_message: bytes,
    *,
    authserv_id: str | None = None,
    trusted_arc_sealers: list[str] | None = None,
    timeout_seconds: float | None = None,
    dnsfunc: DnsFunc | None = None,
) -> MailAuthResult:
    """REQ-1.5: verify mail-auth for the IMAP listener.

    Runs synchronous DKIM/ARC verification in a thread with a hard wall-clock
    ceiling (REQ-1.4). Any unhandled exception from the verify libraries is
    caught and mapped to ``reason="malformed_headers"`` (REQ-9, fail-closed).

    The returned :class:`MailAuthResult` carries the parsed ``Message`` so
    callers don't re-parse the raw bytes; ``message_id`` is extracted once
    here for the same reason.
    """
    authserv = authserv_id or settings.imap_authserv_id
    trusted_sealers = {s.lower() for s in (trusted_arc_sealers or settings.imap_trusted_arc_sealers)}
    timeout = timeout_seconds if timeout_seconds is not None else settings.imap_auth_timeout_seconds

    try:
        msg = email.message_from_bytes(raw_message)
    except Exception:
        logger.debug("mail_auth_parse_failed", exc_info=True)
        return _reject("", "", "<unknown>", "malformed_headers", parsed_message=None)

    from_header, from_domain, verified_from_addr = _extract_from(msg)
    message_id = msg.get("Message-ID") or "<unknown>"

    if not from_domain or not verified_from_addr:
        return _reject(from_header, "", message_id, "malformed_headers", parsed_message=msg)

    try:
        dkim_r, arc_r = await asyncio.wait_for(
            asyncio.to_thread(
                _verify_crypto_sync,
                raw_message,
                msg,
                dnsfunc,
                timeout,
                trusted_sealers,
                from_domain,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        return _reject(from_header, from_domain, message_id, "dkim_timeout", parsed_message=msg)
    except Exception:
        logger.debug("mail_auth_verify_unexpected_error", exc_info=True)
        return _reject(from_header, from_domain, message_id, "malformed_headers", parsed_message=msg)

    spf_r = _spf_from_auth_results(_trusted_auth_results(msg, authserv), from_domain)

    reason = _verdict(dkim_r, arc_r)
    return MailAuthResult(
        dkim_result=dkim_r,
        spf_result=spf_r,
        arc_result=arc_r,
        from_header=from_header,
        from_domain=from_domain,
        message_id=message_id,
        verified_from=verified_from_addr if reason == "" else None,
        reason=reason,
        parsed_message=msg,
    )


def _verify_crypto_sync(
    raw: bytes,
    msg: Message,
    dnsfunc: DnsFunc | None,
    timeout: float,
    trusted_sealers: set[str],
    from_domain: str,
) -> tuple[DkimResult, ArcResult]:
    """Run DKIM + ARC verify inside one thread offload. Must not raise."""
    return (
        _dkim_verify_sync(raw, dnsfunc, timeout, from_domain),
        _arc_verify_sync(raw, msg, dnsfunc, timeout, trusted_sealers, from_domain),
    )


def _reject(
    from_header: str,
    from_domain: str,
    message_id: str,
    reason: RejectReason,
    *,
    parsed_message: Message | None,
) -> MailAuthResult:
    """Shortcut for constructing a rejected MailAuthResult with empty sub-results."""
    return MailAuthResult(
        dkim_result=DkimResult(),
        spf_result=SpfResult(),
        arc_result=ArcResult(),
        from_header=from_header,
        from_domain=from_domain,
        message_id=message_id,
        verified_from=None,
        reason=reason,
        parsed_message=parsed_message,
    )


def result_log_fields(auth: MailAuthResult) -> dict[str, Any]:
    """Flatten sub-results to a queryable log-field dict.

    Used by :mod:`app.services.imap_listener` to build the
    ``imap_auth_passed`` / ``imap_auth_failed`` structlog entries without
    callers ever touching :class:`DkimResult` / :class:`SpfResult` /
    :class:`ArcResult` internals directly.
    """
    return {
        "dkim_result": dataclasses.asdict(auth.dkim_result),
        "spf_result": dataclasses.asdict(auth.spf_result),
        "arc_result": dataclasses.asdict(auth.arc_result),
    }
