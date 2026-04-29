"""Parse redis:// URLs without urllib.parse.

Why this exists
---------------
``redis-py``'s ``Redis.from_url(url)`` calls ``urllib.parse.urlparse(url)``,
which fails with an opaque ``ValueError("Port could not be cast to integer
value as '<garbled>'")`` when the password contains characters that
urlparse treats as reserved in the userinfo component — most commonly
``:``, ``/``, ``+``, ``@``, ``#``, and ``?``.

Operators commonly omit URL-encoding when copying a generated password
straight into a SOPS env file. The service then starts cleanly because
``redis_asyncio.from_url`` is called lazily on the first Redis operation,
and only fails at request time. The result is a service that looks
healthy in ``docker ps`` but returns 500 on every webhook — exactly the
2026-04-29 mailer-notify outage.

The fix is to parse the URL ourselves with structural splits instead of
``urlparse``. The password component is treated as opaque bytes between
the first ``:`` after the scheme and the LAST ``@`` before the host.
``redis-py``'s ``Redis(host=..., port=..., password=...)`` constructor
then receives the password as a regular kwarg, with no further URL
parsing applied to it.

Reference
---------
SPEC-SEC-MAILER-INJECTION-001 v1.1 REQ-6 — nonce store with parseable
Redis URL (the original REQ-6 framed Redis as a fail-closed dependency;
v1.1 closes the operator-error class that made the service crash before
it could fail-close).

Pitfall: ``redis-url-password-must-be-parsed-manually`` in
``.claude/rules/klai/pitfalls/process-rules.md``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedRedisURL:
    """Structural breakdown of a ``redis://`` or ``rediss://`` URL.

    Fields map 1:1 to ``redis.asyncio.Redis(**kwargs)`` so callers can
    construct a client without re-parsing.
    """

    scheme: str
    host: str
    port: int
    username: str | None
    password: str | None
    db: int

    @property
    def use_ssl(self) -> bool:
        return self.scheme == "rediss"


class RedisURLError(ValueError):
    """Raised when the input does not look like a redis URL.

    Distinct from ``ValueError`` raised by urlparse so callers can
    distinguish operator misconfiguration ("URL is shaped wrong") from
    runtime parsing accidents.
    """


def parse_redis_url(url: str) -> ParsedRedisURL:
    """Parse a ``redis://`` / ``rediss://`` URL into a structural dict.

    Accepts URLs of the form::

        redis://[username[:password]@]host[:port][/db]
        rediss://[username[:password]@]host[:port][/db]

    The password may contain any character — including ``:``, ``/``,
    ``@``, ``+``, ``%`` — without URL-encoding. The parser uses
    structural splits (``rsplit('@', 1)``, ``partition(':')``) so the
    password is taken as-is from the input.

    Returns ``ParsedRedisURL``. Raises ``RedisURLError`` on a URL that
    is structurally broken (no scheme, no host).

    Defaults:
        port = 6379 when omitted
        db   = 0    when omitted

    Examples (all valid)::

        redis://redis:6379/0
        redis://:plainpassword@redis:6379/0
        redis://:p@s/wo+rd@redis:6379/0       # password contains @, /, +
        rediss://user:pass@cache.example.com  # SSL, no port, no db
    """
    if not url or "://" not in url:
        raise RedisURLError(f"REDIS_URL missing scheme: {url[:30]!r}")

    scheme, rest = url.split("://", 1)
    if scheme not in ("redis", "rediss"):
        raise RedisURLError(f"REDIS_URL unsupported scheme: {scheme!r}")

    username: str | None = None
    password: str | None = None
    if "@" in rest:
        # Split on the LAST '@' so a password containing '@' survives.
        userinfo, rest = rest.rsplit("@", 1)
        if ":" in userinfo:
            # Split on the FIRST ':' so a password containing ':' survives.
            user_part, _, pw_part = userinfo.partition(":")
            username = user_part or None
            password = pw_part or None
        else:
            username = userinfo or None

    # rest is now host[:port][/db]
    if "/" in rest:
        hostport, _, db_str = rest.partition("/")
    else:
        hostport, db_str = rest, ""

    if ":" in hostport:
        host, _, port_str = hostport.partition(":")
        try:
            port = int(port_str)
        except ValueError as exc:
            raise RedisURLError(f"REDIS_URL port is not an integer: {port_str!r}") from exc
    else:
        host, port = hostport, 6379

    if not host:
        raise RedisURLError("REDIS_URL is missing the host component")

    if db_str:
        try:
            db = int(db_str)
        except ValueError as exc:
            raise RedisURLError(f"REDIS_URL db is not an integer: {db_str!r}") from exc
    else:
        db = 0

    return ParsedRedisURL(
        scheme=scheme,
        host=host,
        port=port,
        username=username,
        password=password,
        db=db,
    )
