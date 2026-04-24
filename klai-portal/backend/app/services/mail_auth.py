"""Mail authentication helper for the IMAP calendar listener.

SPEC-SEC-IMAP-001. Verifies DKIM (aligned to RFC-5322 From domain), reads
SPF from the trusted upstream Authentication-Results header, and accepts a
valid ARC chain from a trusted sealer as a substitute for direct DKIM
alignment on forwarded mail. Returns a structured MailAuthResult; callers
gate on ``verified_from is not None``.

Call site: ``app.services.imap_listener._process_email``.
"""

from __future__ import annotations

import asyncio
import email
from collections.abc import Callable
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses
from typing import Any, Literal

import authres
import dkim
import publicsuffix2
import structlog
from authres.core import AuthenticationResultsHeader

from app.core.config import settings

logger = structlog.get_logger()

DnsFunc = Callable[[str], bytes]

# REQ-4.1: every possible value of `MailAuthResult.reason`. Empty string means
# "pass". A Literal type means a typo here is caught by pyright, not by a
# missed test assertion.
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

# Empty result sentinels — used when a signal is absent or unreachable.
_EMPTY_DKIM: dict[str, Any] = {"present": False, "valid": False, "d": None, "aligned": False}
_EMPTY_SPF: dict[str, Any] = {"result": "absent", "smtp_mailfrom_domain": "", "aligned": False}
_EMPTY_ARC: dict[str, Any] = {
    "present": False,
    "valid": False,
    "sealer": None,
    "trusted": False,
    "aligned_from_domain": False,
}


@dataclass(frozen=True)
class MailAuthResult:
    """REQ-1.5: structured mail-auth verdict.

    Downstream callers MUST gate on ``verified_from is not None``. The
    individual result dicts are for logging only; combining them outside this
    module is a footgun (e.g. DKIM valid-but-misaligned is NOT acceptance).
    """

    dkim_result: dict[str, Any]
    spf_result: dict[str, Any]
    arc_result: dict[str, Any]
    from_header: str
    from_domain: str
    verified_from: str | None
    reason: RejectReason


def _organizational_domain(domain: str) -> str:
    """RFC 7489 §3.1.1 organizational domain via the Public Suffix List.

    A naive two-label heuristic misaligns on ccTLDs with public suffixes — e.g.
    ``evil.co.uk`` and ``target.co.uk`` both yield ``co.uk`` and therefore
    would "align". ``publicsuffix2`` consults the IANA-maintained PSL and
    returns the correct organizational domain (``evil.co.uk`` → ``evil.co.uk``,
    ``mail.customer.nl`` → ``customer.nl``).
    """
    sld = publicsuffix2.get_sld(domain.lower().strip("."))
    # get_sld returns None for pure public suffixes or unknown TLDs — fall
    # back to the input so alignment still produces a defensible answer
    # (callers then compare two strings, one of which is the original domain).
    return sld or domain.lower().strip(".")


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


def _dkim_verify_sync(raw: bytes, dnsfunc: DnsFunc | None, timeout: float) -> dict[str, Any]:
    """REQ-1.2: crypto-verify the first DKIM-Signature. Returns structured result.

    ``aligned`` is set to False here — the caller computes alignment against
    the RFC-5322 From domain once both values are known.
    """
    has_header = b"dkim-signature:" in raw.lower()
    if not has_header:
        return dict(_EMPTY_DKIM)
    d = dkim.DKIM(raw, timeout=int(timeout))
    try:
        valid = d.verify(dnsfunc=dnsfunc) if dnsfunc is not None else d.verify()
    except dkim.DKIMException:
        return {"present": True, "valid": False, "d": None, "aligned": False}
    signing = d.domain.decode().lower() if d.domain else None
    return {"present": True, "valid": bool(valid), "d": signing, "aligned": False}


def _arc_verify_sync(
    raw: bytes,
    msg: Message,
    dnsfunc: DnsFunc | None,
    timeout: float,
    trusted_sealers: set[str],
    from_domain: str,
) -> dict[str, Any]:
    """REQ-3: verify ARC chain and check innermost Auth-Results for aligned DKIM."""
    has_header = b"arc-seal:" in raw.lower()
    if not has_header:
        return dict(_EMPTY_ARC)
    a = dkim.ARC(raw, timeout=int(timeout))
    try:
        cv, _results, _reason = a.verify(dnsfunc=dnsfunc) if dnsfunc is not None else a.verify()
    except dkim.DKIMException:
        return {**_EMPTY_ARC, "present": True, "valid": False}

    valid = cv == dkim.CV_Pass
    sealer = a.domain.decode().lower() if a.domain else None
    trusted = sealer in trusted_sealers if sealer else False
    aligned_from = valid and trusted and _arc_inner_dkim_aligned(msg, from_domain)
    return {
        "present": True,
        "valid": valid,
        "sealer": sealer,
        "trusted": trusted,
        "aligned_from_domain": aligned_from,
    }


def _arc_inner_dkim_aligned(msg: Message, from_domain: str) -> bool:
    """REQ-3.2: check the innermost ARC-Authentication-Results for aligned DKIM=pass.

    Innermost hop = lowest ``i=`` value in the chain. Per RFC 8617 §4.2, the
    ARC-Authentication-Results header is prefixed with ``i=N;`` followed by
    an ordinary Authentication-Results body.
    """
    parsed: list[tuple[int, AuthenticationResultsHeader]] = []
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


def _trusted_auth_results(msg: Message, authserv_id: str) -> list[AuthenticationResultsHeader]:
    """Return only Authentication-Results stamped by the configured authserv-id.

    REQ-2.4 trust boundary: a sender-injected Authentication-Results header is
    attacker-controlled and MUST be ignored.
    """
    out: list[AuthenticationResultsHeader] = []
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


def _spf_from_auth_results(ars: list[AuthenticationResultsHeader], from_domain: str) -> dict[str, Any]:
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
            return {
                "result": result or "absent",
                "smtp_mailfrom_domain": mailfrom_domain,
                "aligned": result == "pass" and _aligned(mailfrom_domain, from_domain),
            }
    return dict(_EMPTY_SPF)


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
    """
    authserv = authserv_id or settings.imap_authserv_id
    trusted_sealers = {s.lower() for s in (trusted_arc_sealers or settings.imap_trusted_arc_sealers)}
    timeout = timeout_seconds if timeout_seconds is not None else settings.imap_auth_timeout_seconds

    try:
        msg = email.message_from_bytes(raw_message)
    except Exception:
        # REQ-9 fail-closed: any parse error means we cannot trust the envelope.
        logger.debug("mail_auth_parse_failed", exc_info=True)
        return _reject("", "", "malformed_headers")

    from_header, from_domain, verified_from_addr = _extract_from(msg)
    if not from_domain or not verified_from_addr:
        return _reject(from_header, "", "malformed_headers")

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
        return _reject(from_header, from_domain, "dkim_timeout")
    except Exception:
        # REQ-9 fail-closed: any unhandled verify error → reject as malformed.
        logger.debug("mail_auth_verify_unexpected_error", exc_info=True)
        return _reject(from_header, from_domain, "malformed_headers")

    # Alignment check: cheap string-compare; kept out of the threaded block.
    if dkim_r["valid"] and dkim_r["d"]:
        dkim_r["aligned"] = _aligned(dkim_r["d"], from_domain)

    spf_r = _spf_from_auth_results(_trusted_auth_results(msg, authserv), from_domain)

    reason = _verdict(dkim_r, arc_r)
    if not reason:
        return MailAuthResult(
            dkim_result=dkim_r,
            spf_result=spf_r,
            arc_result=arc_r,
            from_header=from_header,
            from_domain=from_domain,
            verified_from=verified_from_addr,
            reason="",
        )
    return MailAuthResult(
        dkim_result=dkim_r,
        spf_result=spf_r,
        arc_result=arc_r,
        from_header=from_header,
        from_domain=from_domain,
        verified_from=None,
        reason=reason,
    )


def _verify_crypto_sync(
    raw: bytes,
    msg: Message,
    dnsfunc: DnsFunc | None,
    timeout: float,
    trusted_sealers: set[str],
    from_domain: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run DKIM + ARC verify inside one thread offload. Must not raise."""
    return (
        _dkim_verify_sync(raw, dnsfunc, timeout),
        _arc_verify_sync(raw, msg, dnsfunc, timeout, trusted_sealers, from_domain),
    )


def _verdict(dkim_r: dict[str, Any], arc_r: dict[str, Any]) -> RejectReason:
    """Return empty string on accept, REQ-4.1 reason code on reject.

    Accept iff one of:
    - DKIM valid AND aligned to From domain (REQ-1.2)
    - ARC valid AND sealer trusted AND inner DKIM aligned to From (REQ-3.2)
    """
    if dkim_r["valid"] and dkim_r["aligned"]:
        return ""
    if arc_r["valid"] and arc_r["trusted"] and arc_r["aligned_from_domain"]:
        return ""

    # Ordered most-specific-first so log `reason` codes are actionable.
    if arc_r["present"] and arc_r["valid"] and not arc_r["trusted"]:
        return "arc_untrusted_sealer"
    if arc_r["present"] and not arc_r["valid"]:
        return "arc_invalid"
    if dkim_r["present"] and dkim_r["valid"] and not dkim_r["aligned"]:
        return "dkim_misaligned"
    if dkim_r["present"] and not dkim_r["valid"]:
        return "dkim_invalid"
    if not dkim_r["present"] and not arc_r["present"]:
        return "no_dkim_signature"
    return "no_auth_signal"


def _reject(from_header: str, from_domain: str, reason: RejectReason) -> MailAuthResult:
    """Shortcut for constructing a rejected MailAuthResult with empty sub-results."""
    return MailAuthResult(
        dkim_result=dict(_EMPTY_DKIM),
        spf_result=dict(_EMPTY_SPF),
        arc_result=dict(_EMPTY_ARC),
        from_header=from_header,
        from_domain=from_domain,
        verified_from=None,
        reason=reason,
    )
