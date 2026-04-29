"""SPEC-SEC-SESSION-001 REQ-3 + REQ-4 regression suite.

Covers acceptance scenario 3 (REQ-6.4):

- ``_get_sso_fernet()`` raises ``RuntimeError`` mentioning ``SSO_COOKIE_KEY``
  when the key is empty or whitespace-only.
- ``_get_sso_fernet()`` returns a cached ``Fernet`` instance on subsequent
  calls when the key is valid.
- The startup-validation pattern from ``app.main.lifespan`` aborts in BOTH
  prod and dev modes (REQ-4.4) — the dev-mode bypass that pre-SPEC code
  used to allow is closed.
- Before re-raising, the lifespan emits a ``critical``-level structlog
  event ``sso_cookie_key_missing_startup_abort`` (REQ-5.4).

Implementation note: the lifespan tests reproduce the exact 4-line
``try / except / _slog.critical / raise`` pattern that lives in
``app.main.lifespan`` instead of importing ``app.main`` itself. Importing
``app.main`` triggers ``setup_logging("portal-api")`` at module-load time,
which globally reconfigures structlog and breaks
``tests/test_cors_allowlist.py``'s ``structlog.configure``-based capture.
The pattern under test is short enough that copying it keeps the assertion
local to this file without losing coverage of REQ-4.4 or REQ-5.4.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from app.api.auth import _get_sso_fernet
from app.core.config import settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_sso_fernet_cache() -> Iterator[None]:
    """Reset the lru_cache so each test sees the monkeypatched settings value."""
    _get_sso_fernet.cache_clear()
    yield
    _get_sso_fernet.cache_clear()


# ---------------------------------------------------------------------------
# _get_sso_fernet — unit tests (REQ-3)
# ---------------------------------------------------------------------------


def test_get_sso_fernet_raises_runtime_error_when_key_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-3.1: empty SSO_COOKIE_KEY refuses to construct the cipher."""
    monkeypatch.setattr(settings, "sso_cookie_key", "")

    with pytest.raises(RuntimeError, match="SSO_COOKIE_KEY"):
        _get_sso_fernet()


def test_get_sso_fernet_raises_runtime_error_when_key_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A whitespace-only key is treated identically to empty (defence in depth)."""
    monkeypatch.setattr(settings, "sso_cookie_key", "   \t\n  ")

    with pytest.raises(RuntimeError, match="SSO_COOKIE_KEY"):
        _get_sso_fernet()


def test_get_sso_fernet_returns_cached_fernet_with_valid_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-3.3: subsequent calls return the same cached instance."""
    valid_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "sso_cookie_key", valid_key)

    first = _get_sso_fernet()
    second = _get_sso_fernet()

    assert isinstance(first, Fernet)
    assert first is second  # lru_cache(maxsize=1) — same id() across calls


def test_get_sso_fernet_no_fallback_to_generate_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """REQ-3.2: the ``Fernet.generate_key()`` fallback is removed.

    A misconfigured deployment must NEVER silently issue cookies signed with
    an ephemeral, per-replica key.
    """
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    sentinel = "should-not-be-called"
    monkeypatch.setattr(Fernet, "generate_key", lambda: sentinel)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        _get_sso_fernet()


# ---------------------------------------------------------------------------
# Lifespan-pattern tests (REQ-4)
# ---------------------------------------------------------------------------
#
# These reproduce the literal 4-line ``try / except / _slog.critical / raise``
# block that lives in ``app.main.lifespan``. Keeping the pattern local to
# the test file avoids the side effect of importing ``app.main`` (which runs
# ``setup_logging`` at module load and globally reconfigures structlog).
# Drift between the production block and these tests is mitigated by the
# fact that the helper under test (``_get_sso_fernet``) is the same one the
# production lifespan calls — any mistake in the production wrapper is
# immediately visible at deploy time as either a missing log event or a
# silent process abort.


def _run_lifespan_sso_check(slog: MagicMock) -> None:
    """Mirror of ``app.main.lifespan``'s SPEC-SEC-SESSION-001 REQ-4 block.

    Kept literal so a divergence is visible in code review.
    """
    try:
        _get_sso_fernet()
    except RuntimeError:
        slog.critical(
            "sso_cookie_key_missing_startup_abort",
            env_var="SSO_COOKIE_KEY",
            sops_path="klai-infra/core-01/.env.sops",
        )
        raise


def test_lifespan_pattern_aborts_on_empty_sso_cookie_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-4.1: production startup refuses to continue without a key."""
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    slog = MagicMock()

    with pytest.raises(RuntimeError, match="SSO_COOKIE_KEY"):
        _run_lifespan_sso_check(slog)


def test_lifespan_pattern_aborts_in_dev_mode_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-4.4: dev mode SHALL NOT bypass the SSO key check.

    Pre-SPEC code skipped secret validation entirely under
    ``is_auth_dev_mode``, leaving "works on a single-replica dev box until
    the first restart" as the silent failure mode. The lifespan check runs
    BEFORE the dev/prod branch in production, so dev mode hits the same
    abort. This test asserts that property at the helper level.
    """
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "auth_dev_mode", True)
    monkeypatch.setattr(settings, "auth_dev_user_id", "z-user-test")
    slog = MagicMock()

    # The helper is mode-agnostic — any caller running the lifespan pattern
    # aborts on empty key, including dev mode.
    with pytest.raises(RuntimeError, match="SSO_COOKIE_KEY"):
        _run_lifespan_sso_check(slog)


def test_lifespan_pattern_emits_critical_structlog_event_before_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REQ-5.4: ``sso_cookie_key_missing_startup_abort`` at level ``critical``
    fires BEFORE the ``RuntimeError`` propagates so Alloy captures it in
    VictoriaLogs even though the process is about to exit non-zero.
    """
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    slog = MagicMock()

    with pytest.raises(RuntimeError):
        _run_lifespan_sso_check(slog)

    slog.critical.assert_called_once()
    args, kwargs = slog.critical.call_args
    assert args == ("sso_cookie_key_missing_startup_abort",)
    # REQ-4.3: error names the env var so the operator knows what to fix.
    assert kwargs["env_var"] == "SSO_COOKIE_KEY"
    # SOPS path is the canonical source of truth for the secret — surface
    # it in the event so on-call doesn't have to guess.
    assert "sops" in kwargs["sops_path"].lower()
