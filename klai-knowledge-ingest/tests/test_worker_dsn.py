"""Unit tests for ``knowledge_ingest.worker._build_libpq_dsn``.

The DSN rewrite is the trickiest part of the worker bootstrap — it has
to handle base64 passwords with ``=``, ``+``, ``/`` chars that break
both stdlib urlparse and libpq key=value parsing. Pin the behaviour so
a future "improvement" cannot silently regress it.
"""

from __future__ import annotations

from knowledge_ingest.worker import _build_libpq_dsn


def test_simple_dsn_round_trip():
    dsn = "postgresql+asyncpg://klai:simplepw@postgres:5432/klai"
    out = _build_libpq_dsn(dsn)
    assert out == "host=postgres port=5432 dbname=klai user=klai password='simplepw'"


def test_base64_password_with_equals_is_quoted():
    """libpq treats unquoted ``=`` as a key=value separator. Wrapping in
    single quotes forces it to be parsed as part of the password.
    """
    dsn = "postgresql+asyncpg://klai:abcDEF==@postgres:5432/klai"
    out = _build_libpq_dsn(dsn)
    assert "password='abcDEF=='" in out


def test_password_with_slash_is_preserved():
    dsn = "postgresql+asyncpg://klai:foo/bar+baz=@postgres:5432/klai"
    out = _build_libpq_dsn(dsn)
    assert "password='foo/bar+baz='" in out


def test_password_with_single_quote_is_escaped():
    dsn = "postgresql+asyncpg://klai:fo'o@postgres:5432/klai"
    out = _build_libpq_dsn(dsn)
    # Single quote inside the password is escaped to ``\'`` so libpq
    # does not see it as the closing quote.
    assert r"password='fo\'o'" in out


def test_password_with_backslash_is_escaped():
    dsn = r"postgresql+asyncpg://klai:fo\bar@postgres:5432/klai"
    out = _build_libpq_dsn(dsn)
    # Backslash inside libpq quoted strings must be doubled.
    assert r"password='fo\\bar'" in out


def test_default_port_when_missing():
    dsn = "postgresql+asyncpg://klai:pw@postgres/klai"
    out = _build_libpq_dsn(dsn)
    assert "port=5432" in out


def test_uses_database_name_from_url():
    dsn = "postgresql+asyncpg://klai:pw@postgres:5432/anotherdb"
    out = _build_libpq_dsn(dsn)
    assert "dbname=anotherdb" in out


def test_uses_username_from_url():
    dsn = "postgresql+asyncpg://different_user:pw@postgres:5432/klai"
    out = _build_libpq_dsn(dsn)
    assert "user=different_user" in out


def test_empty_password_renders_as_empty_quoted_string():
    """An empty password is valid; it must produce ``password=''`` (libpq's
    way to assert no password) rather than crashing."""
    dsn = "postgresql+asyncpg://klai:@postgres:5432/klai"
    out = _build_libpq_dsn(dsn)
    assert "password=''" in out
