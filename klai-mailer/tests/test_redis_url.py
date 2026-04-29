"""Tests for ``app.redis_url.parse_redis_url``.

Regression coverage for the 2026-04-29 mailer-notify outage (SPEC-SEC-
MAILER-INJECTION-001 v1.1 REQ-6 hardening). The original code path
used ``redis_asyncio.from_url(url)`` which delegates to
``urllib.parse.urlparse``, raising
``ValueError("Port could not be cast to integer value as '<garbled>'")``
on URLs whose password contains reserved characters (``:``, ``/``,
``+``, ``@``). The parser ``parse_redis_url`` peels off the userinfo
structurally so the password is taken as opaque bytes.
"""

from __future__ import annotations

import pytest

from app.redis_url import ParsedRedisURL, RedisURLError, parse_redis_url

# ---------------------------------------------------------------------------
# Happy path: well-formed URLs
# ---------------------------------------------------------------------------


class TestParseHappyPath:
    """Common production-shaped URLs parse to the expected ParsedRedisURL."""

    def test_minimal_host_only(self) -> None:
        result = parse_redis_url("redis://redis")
        assert result == ParsedRedisURL(
            scheme="redis", host="redis", port=6379, username=None, password=None, db=0
        )

    def test_host_port_db(self) -> None:
        result = parse_redis_url("redis://redis:6379/0")
        assert result.host == "redis"
        assert result.port == 6379
        assert result.db == 0
        assert result.password is None

    def test_password_only_no_user(self) -> None:
        """``redis://:password@host`` — empty username, password set."""
        result = parse_redis_url("redis://:plainpassword@redis:6379/0")
        assert result.username is None
        assert result.password == "plainpassword"
        assert result.host == "redis"
        assert result.port == 6379

    def test_user_and_password(self) -> None:
        result = parse_redis_url("redis://default:plainpassword@redis:6379/2")
        assert result.username == "default"
        assert result.password == "plainpassword"
        assert result.db == 2

    def test_rediss_ssl_scheme(self) -> None:
        result = parse_redis_url("rediss://user:pass@cache.example.com")
        assert result.use_ssl is True
        assert result.host == "cache.example.com"
        assert result.port == 6379  # default

    def test_default_port_when_omitted(self) -> None:
        result = parse_redis_url("redis://:secret@redis/0")
        assert result.port == 6379

    def test_default_db_when_omitted(self) -> None:
        result = parse_redis_url("redis://:secret@redis:6379")
        assert result.db == 0


# ---------------------------------------------------------------------------
# Reserved-character regression cases — the 2026-04-29 outage class
# ---------------------------------------------------------------------------


class TestParsePasswordWithReservedChars:
    """REQ-6.1 (v1.1): passwords containing characters that urllib would
    treat as URL-reserved must survive the parser unescaped."""

    def test_password_with_colon_does_not_become_port(self) -> None:
        """The exact 2026-04-29 outage shape: ``:hPKBf`` looked like a port
        to urllib; structural parsing keeps it as password text."""
        result = parse_redis_url("redis://:p:hPKBf@redis:6379/0")
        assert result.password == "p:hPKBf"
        assert result.port == 6379
        assert result.host == "redis"

    def test_password_with_slash(self) -> None:
        result = parse_redis_url("redis://:abc/def/ghi@redis:6379/0")
        assert result.password == "abc/def/ghi"
        assert result.db == 0

    def test_password_with_plus(self) -> None:
        result = parse_redis_url("redis://:abc+def@redis:6379/0")
        assert result.password == "abc+def"

    def test_password_with_at_sign(self) -> None:
        """``@`` in password — rsplit on the LAST ``@`` keeps it intact."""
        result = parse_redis_url("redis://:abc@xyz@redis:6379/0")
        assert result.password == "abc@xyz"
        assert result.host == "redis"

    def test_password_with_all_reserved_chars(self) -> None:
        """Worst-case regression: every reserved char in one password."""
        weird_pw = "a:b/c+d@e#f?g%h&i"
        url = f"redis://:{weird_pw}@redis:6379/0"
        result = parse_redis_url(url)
        assert result.password == weird_pw
        assert result.host == "redis"
        assert result.port == 6379

    def test_password_starting_with_colon_treated_as_empty_user(self) -> None:
        """``redis://:somepw@host`` is the canonical SOPS shape."""
        result = parse_redis_url("redis://:s:e:c@redis:6379/0")
        assert result.username is None
        assert result.password == "s:e:c"


# ---------------------------------------------------------------------------
# Error paths: structurally-broken URLs raise RedisURLError
# ---------------------------------------------------------------------------


class TestParseStructuralErrors:
    """Truly malformed URLs raise ``RedisURLError`` (a ``ValueError``).

    These are operator-bug states; the caller (``get_redis``) translates
    them into ``RedisUnavailableError`` so /notify returns 503.
    """

    def test_empty_string(self) -> None:
        with pytest.raises(RedisURLError):
            parse_redis_url("")

    def test_missing_scheme(self) -> None:
        with pytest.raises(RedisURLError):
            parse_redis_url("redis-no-scheme")

    def test_unsupported_scheme(self) -> None:
        with pytest.raises(RedisURLError):
            parse_redis_url("memcached://host:11211")

    def test_missing_host(self) -> None:
        with pytest.raises(RedisURLError):
            parse_redis_url("redis://")

    def test_non_integer_port(self) -> None:
        """Even with structural parsing, the port itself must be a number.

        Operators who ALSO put reserved chars after the host:port boundary
        get a clear RedisURLError, not a downstream confusion.
        """
        with pytest.raises(RedisURLError):
            parse_redis_url("redis://redis:not-a-number/0")

    def test_non_integer_db(self) -> None:
        with pytest.raises(RedisURLError):
            parse_redis_url("redis://redis:6379/notadb")

    def test_redis_url_error_is_value_error(self) -> None:
        """RedisURLError must subclass ValueError so legacy
        ``except ValueError:`` callers still match."""
        try:
            parse_redis_url("")
        except ValueError as exc:
            assert isinstance(exc, RedisURLError)


# ---------------------------------------------------------------------------
# Boundary cases: empty user/password components
# ---------------------------------------------------------------------------


class TestEmptyComponents:
    """Empty-string components are normalised to None so kwargs unpack
    cleanly to ``Redis(host=..., username=None, ...)`` without sending
    spurious empty values."""

    def test_empty_password_becomes_none(self) -> None:
        result = parse_redis_url("redis://user:@redis:6379/0")
        assert result.username == "user"
        assert result.password is None

    def test_empty_user_becomes_none(self) -> None:
        result = parse_redis_url("redis://:password@redis:6379/0")
        assert result.username is None
        assert result.password == "password"

    def test_no_userinfo_section(self) -> None:
        result = parse_redis_url("redis://redis:6379/0")
        assert result.username is None
        assert result.password is None
