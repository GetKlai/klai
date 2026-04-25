"""Test fixtures for SPEC-SEC-IMAP-001.

Builders generate raw RFC-822 bytes for the IMAP listener mail-auth tests.
No disk writes — every fixture is produced in-memory from a throwaway RSA
key owned by the test suite. A companion ``make_dnsfunc`` returns a
dkimpy-compatible DNS resolver stub that serves the matching public key.

This module is imported by both unit tests (which mock dkim.DKIM /
dkim.ARC at the boundary) and integration tests (which let dkimpy
actually verify the crypto against the throwaway key). The builders
themselves produce real DKIM-signed bytes; tests that want to bypass
crypto simply construct the message without calling ``dkim_sign``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import dkim
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@dataclass(frozen=True)
class TestKey:
    """An RSA keypair bound to a (domain, selector) for DKIM signing."""

    private_pem: bytes
    public_b64: bytes
    selector: bytes
    domain: bytes


_KEY_CACHE: dict[tuple[bytes, bytes], TestKey] = {}


def key_for(domain: str, selector: str = "test") -> TestKey:
    """Return a cached RSA keypair for (domain, selector).

    Keys persist for the lifetime of the test process so a dnsfunc built
    at the start of a test serves the matching public key for every
    subsequent signed message.
    """
    ident = (domain.encode(), selector.encode())
    cached = _KEY_CACHE.get(ident)
    if cached is not None:
        return cached

    # 2048-bit throwaway key — test suite only; never reused in production.
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_der = priv.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key = TestKey(
        private_pem=priv_pem,
        public_b64=base64.b64encode(pub_der),
        selector=ident[1],
        domain=ident[0],
    )
    _KEY_CACHE[ident] = key
    return key


def make_dnsfunc(*keys: TestKey):
    """Build a dkimpy-compatible dnsfunc that resolves the given test keys.

    dkimpy calls dnsfunc with a name like ``test._domainkey.example.com.``
    and expects bytes shaped ``v=DKIM1; k=rsa; p=<base64>``. Unknown
    names return empty bytes.
    """
    records: dict[bytes, bytes] = {}
    for k in keys:
        name = k.selector + b"._domainkey." + k.domain + b"."
        records[name] = b"v=DKIM1; k=rsa; p=" + k.public_b64

    def dnsfunc(name, *_args, **_kwargs) -> bytes:
        key = name.encode() if isinstance(name, str) else name
        return records.get(key, b"")

    return dnsfunc


def build_email(
    *,
    from_addr: str = "sender@example.com",
    to_addr: str = "meet@getklai.com",
    subject: str = "Calendar invite",
    body: str = "placeholder body\r\n",
    message_id: str | None = None,
    extra_headers: list[tuple[str, str]] | None = None,
) -> bytes:
    """Assemble a minimal RFC-822 message. Returns raw CRLF bytes."""
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = message_id or make_msgid(domain="test.local")
    for name, value in extra_headers or []:
        msg[name] = value
    msg.set_content(body)
    return msg.as_bytes(policy=msg.policy.clone(linesep="\r\n"))


def dkim_sign(raw: bytes, signing_domain: str, selector: str = "test") -> bytes:
    """Prepend a DKIM-Signature header signed by ``key_for(signing_domain)``."""
    k = key_for(signing_domain, selector)
    sig = dkim.sign(
        message=raw,
        selector=k.selector,
        domain=k.domain,
        privkey=k.private_pem,
        include_headers=[b"From", b"To", b"Subject", b"Date", b"Message-ID"],
    )
    return sig + raw


def arc_sign(
    raw: bytes,
    sealer_domain: str,
    authserv_id: str,
    selector: str = "test",
) -> bytes:
    """Prepend a real ARC-Seal + ARC-Message-Signature + ARC-Authentication-Results.

    Uses the same throwaway RSA key that ``dkim_sign`` uses for the sealing
    domain. The inner ``ARC-Authentication-Results`` is built by
    ``dkim.arc_sign`` from any pre-existing ``Authentication-Results``
    headers in ``raw`` whose authserv-id matches ``authserv_id`` — caller
    should include such a header to model an upstream-authenticated forward.
    """
    k = key_for(sealer_domain, selector)
    arc_headers = dkim.arc_sign(
        message=raw,
        selector=k.selector,
        domain=k.domain,
        privkey=k.private_pem,
        srv_id=authserv_id.encode(),
        include_headers=[b"From", b"To", b"Subject", b"Date", b"Message-ID"],
    )
    return b"".join(arc_headers) + raw
