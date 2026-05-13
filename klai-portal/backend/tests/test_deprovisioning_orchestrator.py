"""Unit tests for SPEC-INFRA-TENANT-DELETE-001 Phase 4 — deprovisioning orchestrator.

Tests cover:
- `deprovision_tenant` entry point (session lifecycle)
- `_run_step_with_retry` retry logic (retryable vs non-retryable)
- `_mark_failed` state transition + last_failure JSONB
- `_resolve_litellm_team_id` lookup
- `_resolve_zitadel_oidc_app_id` lookup
- End-to-end happy path: all steps run, success logged
- End-to-end failure path: step raises → failed_deprovisioning

All external calls (DB, LiteLLM, Zitadel, step functions) are mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest

from app.services.provisioning.deprovisioning_orchestrator import (
    DeprovisionStepError,
    _DeprovisionState,
    _mark_failed,
    _resolve_litellm_team_id,
    _resolve_zitadel_oidc_app_id,
    _run,
    _run_step_with_retry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> _DeprovisionState:
    db = AsyncMock()
    defaults = {
        "db": db,
        "org_id": 42,
        "slug": "acme",
        "zitadel_org_id": "zit-org",
        "zitadel_oidc_app_id": "zit-app",
        "litellm_team_id": "team-001",
        "moneybird_subscription_id": None,
        "moneybird_contact_id": None,
        "deprovisioner_user_id": "user-1",
        "deprovisioner_type": "owner",
        "org_name": "ACME Corp",
    }
    defaults.update(overrides)
    return _DeprovisionState(**defaults)


async def _noop_step(state: _DeprovisionState) -> None:
    """A step that always succeeds."""


async def _fail_step(state: _DeprovisionState) -> None:
    """A step that always raises ValueError (non-retryable)."""
    raise ValueError("step broken")


async def _retryable_fail_step(state: _DeprovisionState) -> None:
    """A step that raises a retryable exception every time."""
    raise asyncpg.PostgresError("connection lost")


# ---------------------------------------------------------------------------
# DeprovisionStepError
# ---------------------------------------------------------------------------


class TestDeprovisionStepError:
    def test_stores_step_name_and_original(self) -> None:
        original = ValueError("boom")
        err = DeprovisionStepError("my_step", original)
        assert err.step_name == "my_step"
        assert err.original is original

    def test_str_includes_step_and_message(self) -> None:
        err = DeprovisionStepError("step_x", RuntimeError("oops"))
        assert "step_x" in str(err)
        assert "oops" in str(err)


# ---------------------------------------------------------------------------
# _run_step_with_retry — success
# ---------------------------------------------------------------------------


class TestRunStepWithRetrySuccess:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self) -> None:
        """A step that succeeds immediately must not sleep or retry."""
        state = _make_state()
        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await _run_step_with_retry(_noop_step, state)
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_after_one_retry(self) -> None:
        """A step that fails once then succeeds must not raise."""
        call_count = 0

        async def _flaky_step(state: _DeprovisionState) -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise asyncpg.PostgresError("blip")

        state = _make_state()
        with patch("asyncio.sleep", new=AsyncMock()):
            await _run_step_with_retry(_flaky_step, state)

        assert call_count == 2


# ---------------------------------------------------------------------------
# _run_step_with_retry — retryable exhaustion
# ---------------------------------------------------------------------------


class TestRunStepWithRetryExhaustion:
    @pytest.mark.asyncio
    async def test_retryable_exhaustion_raises_deprovision_step_error(self) -> None:
        """After 3 failed attempts, DeprovisionStepError must be raised."""
        state = _make_state()
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(DeprovisionStepError) as exc_info:
                await _run_step_with_retry(_retryable_fail_step, state)

        assert exc_info.value.step_name == "_retryable_fail_step"
        assert isinstance(exc_info.value.original, asyncpg.PostgresError)

    @pytest.mark.asyncio
    async def test_retryable_sleeps_between_attempts(self) -> None:
        """sleep is called between retry attempts with correct delays."""
        state = _make_state()
        with patch(
            "app.services.provisioning.deprovisioning_orchestrator.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with pytest.raises(DeprovisionStepError):
                await _run_step_with_retry(_retryable_fail_step, state)

        # 3 attempts = 2 sleeps (before attempts 2 and 3)
        assert mock_sleep.await_count == 2
        delays = [call.args[0] for call in mock_sleep.await_args_list]
        assert delays == [1, 2]


# ---------------------------------------------------------------------------
# _run_step_with_retry — non-retryable
# ---------------------------------------------------------------------------


class TestRunStepWithRetryNonRetryable:
    @pytest.mark.asyncio
    async def test_non_retryable_fails_immediately(self) -> None:
        """ValueError must bypass retries and raise DeprovisionStepError immediately."""
        state = _make_state()
        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            with pytest.raises(DeprovisionStepError) as exc_info:
                await _run_step_with_retry(_fail_step, state)

        mock_sleep.assert_not_awaited()
        assert exc_info.value.step_name == "_fail_step"
        assert isinstance(exc_info.value.original, ValueError)


# ---------------------------------------------------------------------------
# _mark_failed
# ---------------------------------------------------------------------------


class TestMarkFailed:
    @pytest.mark.asyncio
    async def test_transitions_state_and_updates_last_failure(self) -> None:
        """Transition to failed_deprovisioning and populate last_failure JSONB."""
        db = AsyncMock()

        with patch(
            "app.services.provisioning.state_machine.transition_state",
            new=AsyncMock(),
        ) as mock_transition:
            await _mark_failed(db, org_id=42, step_name="step_x", error_str="boom")

        mock_transition.assert_awaited_once()
        kwargs = mock_transition.call_args.kwargs
        assert kwargs["to_state"] == "failed_deprovisioning"
        assert kwargs["from_state"] == "deprovisioning"
        # db.execute called for UPDATE last_failure
        db.execute.assert_awaited_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_in_transition_is_logged_not_raised(self) -> None:
        """An exception inside _mark_failed must NOT propagate."""
        db = AsyncMock()
        db.commit.side_effect = Exception("db down")

        with patch(
            "app.services.provisioning.state_machine.transition_state",
            new=AsyncMock(),
        ):
            # Must not raise
            await _mark_failed(db, org_id=42, step_name="step_x", error_str="boom")

    @pytest.mark.asyncio
    async def test_mark_failed_writes_dict_as_jsonb(self) -> None:
        """SPEC-INFRA-TENANT-DELETE-003 REQ-2 — _mark_failed must serialise the
        failure dict in a way asyncpg can bind to a jsonb column.

        Regression: prior implementation passed a Python ``dict`` directly as a
        text() bind value, which asyncpg rejects with
        ``DataError: 'dict' object has no attribute 'encode'``. The DataError
        was swallowed by the outer try/except so ``last_failure`` stayed NULL
        in production. The fix must encode the dict to a JSON string and CAST
        on the SQL side, so the dict-bind path no longer fires.
        """
        captured: dict[str, object] = {}

        async def fake_execute(stmt, params=None):
            # transition_state is patched out below, so the only db.execute
            # we see here is the UPDATE portal_orgs SET last_failure = ...
            # bound to a dict in the buggy code path.
            if params is None:
                return MagicMock()
            captured["params"] = params
            captured["stmt"] = stmt
            # Simulate asyncpg's actual behaviour: a raw dict bound to a
            # jsonb column raises DataError because dicts have no .encode().
            for key, val in params.items():
                if isinstance(val, dict):
                    raise asyncpg.exceptions.DataError(
                        f"invalid input for query argument ${key}: {val!r} ('dict' object has no attribute 'encode')"
                    )
            return MagicMock()

        db = AsyncMock()
        db.execute.side_effect = fake_execute

        with patch(
            "app.services.provisioning.state_machine.transition_state",
            new=AsyncMock(),
        ):
            await _mark_failed(
                db,
                org_id=10,
                step_name="_delete_meilisearch_index",
                error_str="connection refused",
                attempt=3,
            )

        # No DataError leaked + commit reached = the bind value is no longer
        # a dict. Belt+braces assertions: inspect the captured bind shape.
        db.commit.assert_awaited_once()
        assert "params" in captured, "db.execute(UPDATE last_failure) never called"
        params = captured["params"]
        assert isinstance(params, dict)
        # No dict-typed bind values remain — anything containing the failure
        # payload must have been serialised to a string before the call.
        dict_binds = [k for k, v in params.items() if isinstance(v, dict)]
        assert dict_binds == [], (
            f"_mark_failed still passes a dict to asyncpg: {dict_binds}. "
            "Use CAST(:val AS jsonb) with json.dumps(...) or the ORM path."
        )
        # Sanity: the SQL string must CAST the parameter to jsonb so the
        # JSON-string bind reaches the column as jsonb (REQ-2 AC-2.1).
        stmt_text = str(captured["stmt"])
        assert "jsonb" in stmt_text.lower(), f"UPDATE statement must cast bind to jsonb, got: {stmt_text!r}"


# ---------------------------------------------------------------------------
# _resolve_litellm_team_id
# ---------------------------------------------------------------------------


class TestResolveLitellmTeamId:
    @pytest.mark.asyncio
    async def test_returns_team_id_when_found(self) -> None:
        """LiteLLM list response is parsed and correct team_id returned."""
        teams_payload = [
            {"team_alias": "acme", "team_id": "team-abc"},
            {"team_alias": "other", "team_id": "team-xyz"},
        ]
        with patch("app.services.provisioning.deprovisioning_orchestrator.settings") as mock_settings:
            mock_settings.litellm_base_url = "http://litellm:4000"
            mock_settings.litellm_master_key = "key"
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_http = AsyncMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = teams_payload
                mock_resp.raise_for_status = MagicMock()
                mock_http.get = AsyncMock(return_value=mock_resp)
                mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await _resolve_litellm_team_id("acme")

        assert result == "team-abc"

    @pytest.mark.asyncio
    async def test_returns_empty_when_not_found(self) -> None:
        """Empty string returned when slug not in LiteLLM team list."""
        with patch("app.services.provisioning.deprovisioning_orchestrator.settings") as mock_settings:
            mock_settings.litellm_base_url = "http://litellm:4000"
            mock_settings.litellm_master_key = "key"
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_http = AsyncMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = []
                mock_resp.raise_for_status = MagicMock()
                mock_http.get = AsyncMock(return_value=mock_resp)
                mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await _resolve_litellm_team_id("acme")

        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self) -> None:
        """Network failure returns empty string — non-fatal."""
        with patch("app.services.provisioning.deprovisioning_orchestrator.settings") as mock_settings:
            mock_settings.litellm_base_url = "http://litellm:4000"
            mock_settings.litellm_master_key = "key"
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client_class.return_value.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("down"))
                mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await _resolve_litellm_team_id("acme")

        assert result == ""


# ---------------------------------------------------------------------------
# _resolve_zitadel_oidc_app_id
# ---------------------------------------------------------------------------


class TestResolveZitadelOidcAppId:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_client_id(self) -> None:
        """Empty client_id returns empty string without network call."""
        result = await _resolve_zitadel_oidc_app_id(None)
        assert result == ""

        result = await _resolve_zitadel_oidc_app_id("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_app_id_when_found(self) -> None:
        """Correct app_id returned when the matching app is in Zitadel response."""
        apps_payload = {
            "result": [
                {"id": "app-999", "oidcConfig": {"clientId": "client-abc"}},
                {"id": "app-000", "oidcConfig": {"clientId": "client-other"}},
            ]
        }
        with patch("app.services.provisioning.deprovisioning_orchestrator.settings") as mock_settings:
            mock_settings.zitadel_base_url = "https://auth.example.com"
            mock_settings.zitadel_pat = "pat-token"
            mock_settings.zitadel_project_id = "project-1"
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_http = AsyncMock()
                mock_resp = MagicMock()
                mock_resp.json.return_value = apps_payload
                mock_resp.raise_for_status = MagicMock()
                mock_http.post = AsyncMock(return_value=mock_resp)
                mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_http)
                mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await _resolve_zitadel_oidc_app_id("client-abc")

        assert result == "app-999"

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self) -> None:
        """HTTP failure returns empty string — non-fatal."""
        with patch("app.services.provisioning.deprovisioning_orchestrator.settings") as mock_settings:
            mock_settings.zitadel_base_url = "https://auth.example.com"
            mock_settings.zitadel_pat = "pat-token"
            mock_settings.zitadel_project_id = "project-1"
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client_class.return_value.__aenter__ = AsyncMock(side_effect=httpx.ConnectError("down"))
                mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await _resolve_zitadel_oidc_app_id("client-abc")

        assert result == ""


# ---------------------------------------------------------------------------
# _run — integration-like tests with mocked steps and DB
# ---------------------------------------------------------------------------


class TestRunHappyPath:
    @pytest.mark.asyncio
    async def test_all_steps_called_on_success(self) -> None:
        """When all steps succeed, every step in STEPS is called once."""
        mock_org = SimpleNamespace(
            id=42,
            slug="acme",
            zitadel_org_id="zit-org",
            zitadel_librechat_client_id="client-abc",
            litellm_team_id=None,  # not stored in portal_orgs
            moneybird_subscription_id=None,
            moneybird_contact_id=None,
            name="ACME Corp",
        )
        mock_db = AsyncMock()
        mock_db.execute.return_value.scalar_one.return_value = mock_org

        step_calls = []

        async def _tracking_step(state):
            step_calls.append(state.slug)

        fake_steps = [_tracking_step] * 5

        with (
            patch("app.services.provisioning.deprovisioning_orchestrator._load_state") as mock_load,
            patch("app.services.provisioning.deprovisioning_orchestrator.STEPS", new=fake_steps),
        ):
            mock_load.return_value = _make_state(db=mock_db)
            await _run(42, "user-1", "owner", mock_db)

        assert len(step_calls) == 5

    @pytest.mark.asyncio
    async def test_step_failure_calls_mark_failed(self) -> None:
        """When a step fails, _mark_failed is called and remaining steps skipped."""
        fail_step_calls = []
        post_fail_calls = []

        async def _bad_step(state):
            fail_step_calls.append(1)
            raise ValueError("boom")

        async def _after_fail_step(state):
            post_fail_calls.append(1)

        with (
            patch("app.services.provisioning.deprovisioning_orchestrator._load_state") as mock_load,
            patch("app.services.provisioning.deprovisioning_orchestrator.STEPS", new=[_bad_step, _after_fail_step]),
            patch(
                "app.services.provisioning.deprovisioning_orchestrator._mark_failed",
                new=AsyncMock(),
            ) as mock_mark_failed,
        ):
            mock_load.return_value = _make_state()
            await _run(42, "user-1", "owner", AsyncMock())

        assert len(fail_step_calls) == 1
        assert len(post_fail_calls) == 0
        mock_mark_failed.assert_awaited_once()


# ---------------------------------------------------------------------------
# deprovision_tenant — session ownership
# ---------------------------------------------------------------------------


class TestDeprovisionTenant:
    @pytest.mark.asyncio
    async def test_opens_own_db_session(self) -> None:
        """deprovision_tenant must open its own session (AsyncSessionLocal context manager)."""
        from app.services.provisioning.deprovisioning_orchestrator import deprovision_tenant

        with (
            patch("app.services.provisioning.deprovisioning_orchestrator.AsyncSessionLocal") as mock_session_local,
            patch("app.services.provisioning.deprovisioning_orchestrator._run", new=AsyncMock()) as mock_run,
        ):
            mock_db = AsyncMock()
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=False)

            await deprovision_tenant(42, "user-1", "owner")

        mock_session_local.assert_called_once()
        mock_run.assert_awaited_once_with(42, "user-1", "owner", mock_db)
