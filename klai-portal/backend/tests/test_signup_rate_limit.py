"""SPEC-SEC-HYGIENE-001 REQ-19 / AC-19: per-email rate limit on /api/signup.

Direct unit tests for the rate-limit helper at
``app.services.signup_email_rl``. The helper is the building block;
the wiring into ``POST /api/signup`` is verified by a separate
integration test in ``test_signup_email_rl_integration``.

Covers:
- REQ-19.3: email normalisation (lowercase + strip +alias).
- REQ-19.1, REQ-19.2: INCR + EXPIRE flow, 4th attempt blocked.
- REQ-19.4: fail-open when Redis is unreachable.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.signup_email_rl import (
    EMAIL_RL_LIMIT,
    EMAIL_RL_WINDOW_SECONDS,
    check_signup_email_rate_limit,
    email_sha256,
    normalise_email,
)

# REQ-19.3: email normalisation -------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("alice@example.com", "alice@example.com"),
        ("Alice@Example.com", "alice@example.com"),
        ("alice+signup@example.com", "alice@example.com"),
        ("Alice+SignUp@Example.com", "alice@example.com"),
        ("alice+a+b@example.com", "alice@example.com"),  # only first +
        ("  alice@example.com  ", "alice@example.com"),
        ("no-at-sign", "no-at-sign"),  # defensive — never raise
    ],
)
def test_normalise_email(raw: str, expected: str) -> None:
    assert normalise_email(raw) == expected


def test_email_sha256_is_normalised() -> None:
    """Different surface forms collapse to the same sha256 once normalised."""
    assert email_sha256("Mark+signup@voys.nl") == email_sha256("mark@voys.nl")
    assert email_sha256("Mark+signup@voys.nl") != email_sha256("other@voys.nl")


# REQ-19.1 + REQ-19.2: INCR + EXPIRE flow ---------------------------------- #


def _redis_mock(incr_returns: list[int]) -> AsyncMock:
    """Return a mock Redis whose .incr returns sequential values per call."""
    mock = AsyncMock()
    mock.incr = AsyncMock(side_effect=incr_returns)
    mock.expire = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_first_three_attempts_allowed() -> None:
    redis_mock = _redis_mock([1, 2, 3])
    with patch("app.services.signup_email_rl.get_redis_pool", AsyncMock(return_value=redis_mock)):
        for expected_count in (1, 2, 3):
            allowed = await check_signup_email_rate_limit("attacker@example.com")
            assert allowed is True
            del expected_count
    assert redis_mock.incr.await_count == 3
    # EXPIRE only on the first INCR (count == 1).
    redis_mock.expire.assert_awaited_once()
    args, _ = redis_mock.expire.call_args
    assert args[1] == EMAIL_RL_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_fourth_attempt_blocked() -> None:
    redis_mock = _redis_mock([4])
    with patch("app.services.signup_email_rl.get_redis_pool", AsyncMock(return_value=redis_mock)):
        allowed = await check_signup_email_rate_limit("attacker@example.com")
    assert allowed is False, (
        f"REQ-19.1: 4th attempt within window must be blocked. "
        f"limit={EMAIL_RL_LIMIT}"
    )


@pytest.mark.asyncio
async def test_normalisation_shares_counter() -> None:
    """REQ-19.3: case + alias variants share the SAME Redis key."""
    seen_keys: list[str] = []

    async def fake_incr(key: str) -> int:
        seen_keys.append(key)
        return len(seen_keys)

    redis_mock = AsyncMock()
    redis_mock.incr = AsyncMock(side_effect=fake_incr)
    redis_mock.expire = AsyncMock()

    with patch("app.services.signup_email_rl.get_redis_pool", AsyncMock(return_value=redis_mock)):
        await check_signup_email_rate_limit("attacker@example.com")
        await check_signup_email_rate_limit("Attacker@Example.com")
        await check_signup_email_rate_limit("attacker+foo@example.com")

    # All three calls hit the same Redis key.
    assert len(set(seen_keys)) == 1, f"expected single shared key, got {seen_keys!r}"


# REQ-19.4: fail-open ------------------------------------------------------ #


@pytest.mark.asyncio
async def test_fail_open_when_redis_pool_unavailable() -> None:
    """REQ-19.4: get_redis_pool returning None must allow the signup."""
    with patch("app.services.signup_email_rl.get_redis_pool", AsyncMock(return_value=None)):
        allowed = await check_signup_email_rate_limit("attacker@example.com")
    assert allowed is True


@pytest.mark.asyncio
async def test_fail_open_when_redis_call_raises() -> None:
    """REQ-19.4: any Redis-side exception must allow the signup."""
    redis_mock = AsyncMock()
    redis_mock.incr = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch("app.services.signup_email_rl.get_redis_pool", AsyncMock(return_value=redis_mock)):
        allowed = await check_signup_email_rate_limit("attacker@example.com")
    assert allowed is True


# Sanity: constants -------------------------------------------------------- #


def test_constants() -> None:
    assert EMAIL_RL_LIMIT == 3
    assert EMAIL_RL_WINDOW_SECONDS == 24 * 60 * 60


# REQ-19.5: integration with /api/signup ----------------------------------- #


@pytest.mark.asyncio
async def test_endpoint_returns_429_when_rate_limited() -> None:
    """REQ-19.5: when the helper says blocked, the endpoint returns 429
    with the SPEC-mandated detail string AND Zitadel.create_org is NOT
    called (the limit fires BEFORE Zitadel quota consumption).
    """
    from app.api.signup import SignupRequest, signup

    body = SignupRequest(
        first_name="Eve",
        last_name="Attacker",
        email="attacker@example.com",
        password="strong-passphrase-for-test",
        company_name="ACME",
    )

    fake_zitadel = AsyncMock()
    fake_zitadel.create_org = AsyncMock()  # tracked

    with (
        patch(
            "app.api.signup.check_signup_email_rate_limit",
            AsyncMock(return_value=False),
        ),
        patch("app.api.signup.zitadel", fake_zitadel),
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await signup(body=body, background_tasks=AsyncMock(), db=AsyncMock())

    assert exc_info.value.status_code == 429
    assert "Too many signup attempts" in str(exc_info.value.detail)
    assert exc_info.value.detail.endswith("try again tomorrow.")  # type: ignore[union-attr]
    fake_zitadel.create_org.assert_not_called()


@pytest.mark.asyncio
async def test_endpoint_passes_rate_limit_then_proceeds() -> None:
    """REQ-19.5 negative: when the helper says allowed, the endpoint moves
    on to the next step (Zitadel.create_org gets called).
    """
    from app.api.signup import SignupRequest, signup

    body = SignupRequest(
        first_name="Eve",
        last_name="User",
        email="real-user@example.com",
        password="strong-passphrase-for-test",
        company_name="ACME",
    )

    fake_zitadel = AsyncMock()
    # Make create_org raise so we don't have to mock the entire downstream
    # flow — we only care that the call happened (i.e. the RL gate passed).
    fake_zitadel.create_org = AsyncMock(side_effect=RuntimeError("downstream-stub"))

    with (
        patch(
            "app.api.signup.check_signup_email_rate_limit",
            AsyncMock(return_value=True),
        ),
        patch("app.api.signup.zitadel", fake_zitadel),
    ):
        with pytest.raises(Exception):  # noqa: B017 — any downstream exception is fine
            await signup(body=body, background_tasks=AsyncMock(), db=AsyncMock())

    fake_zitadel.create_org.assert_awaited_once()
