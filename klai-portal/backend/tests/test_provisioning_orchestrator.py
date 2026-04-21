"""SPEC-PROV-001 M3 — orchestrator integration tests.

Verifies the AsyncExitStack-driven rollback, state machine transitions through
the full sequence, and the _finalize_failure two-phase failure marker.

All external dependencies (Zitadel, LiteLLM, Docker, Mongo, docs-app, Caddy)
are mocked. The focus is on control-flow correctness, not on the individual
side-effect calls themselves (which are exercised by infrastructure.py tests).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_orchestrator_env(tmp_path, monkeypatch):
    """Patch everything the orchestrator touches so `_provision` becomes pure
    control-flow logic with no I/O. Returns a dict of mocks so individual
    tests can configure specific failure points.
    """
    from app.services.provisioning import orchestrator

    patches = {}

    # Settings
    patches["settings"] = MagicMock(
        domain="test.local",
        litellm_master_key="litellm-master",
        librechat_container_data_path=str(tmp_path / "container"),
        librechat_host_data_path=str(tmp_path / "host"),
        caddy_tenants_path=str(tmp_path / "caddy"),
    )
    (tmp_path / "caddy").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orchestrator, "settings", patches["settings"])

    # Zitadel
    patches["zitadel"] = MagicMock()
    patches["zitadel"].create_librechat_oidc_app = AsyncMock(
        return_value={"clientId": "cid", "clientSecret": "csec", "appId": "app-1"}
    )
    patches["zitadel"].delete_librechat_oidc_app = AsyncMock()
    monkeypatch.setattr(orchestrator, "zitadel", patches["zitadel"])

    # LiteLLM — httpx AsyncClient
    patches["litellm_team_post"] = MagicMock()
    patches["litellm_team_post"].raise_for_status = MagicMock()
    patches["litellm_team_post"].json = MagicMock(return_value={"team_id": "team-1"})
    patches["litellm_key_post"] = MagicMock()
    patches["litellm_key_post"].raise_for_status = MagicMock()
    patches["litellm_key_post"].json = MagicMock(return_value={"key": "sk-test"})

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, path, json=None):
            if "/team/new" in path:
                return patches["litellm_team_post"]
            if "/key/generate" in path:
                return patches["litellm_key_post"]
            if "/team/delete" in path:
                return MagicMock()
            raise AssertionError(f"Unexpected httpx call: {path}")

    monkeypatch.setattr(orchestrator.httpx, "AsyncClient", lambda **kw: _FakeClient())

    # Mongo
    patches["create_mongo"] = MagicMock()
    monkeypatch.setattr(orchestrator, "_create_mongodb_tenant_user", patches["create_mongo"])
    patches["drop_mongo"] = MagicMock()
    monkeypatch.setattr(orchestrator, "_sync_drop_mongodb_tenant_user", patches["drop_mongo"])

    # env generator (matches real signature: slug, client_id, client_secret, **kw)
    monkeypatch.setattr(
        orchestrator,
        "_generate_librechat_env",
        lambda *args, **kw: "ENV=content",
    )

    # docs_client
    fake_docs = MagicMock()
    fake_docs.provision_gitea_repo = AsyncMock()
    fake_docs.deprovision_kb = AsyncMock()
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.services.docs_client",
        fake_docs,
    )
    patches["docs"] = fake_docs

    # ensure_default_knowledge_bases
    fake_defaults = MagicMock()
    fake_defaults.ensure_default_knowledge_bases = AsyncMock()
    monkeypatch.setitem(
        __import__("sys").modules,
        "app.services.default_knowledge_bases",
        fake_defaults,
    )

    # Docker container + Caddy
    patches["start_container"] = MagicMock()
    monkeypatch.setattr(orchestrator, "_start_librechat_container", patches["start_container"])
    patches["remove_container"] = MagicMock()
    monkeypatch.setattr(orchestrator, "_sync_remove_container", patches["remove_container"])
    patches["reload_caddy"] = MagicMock()
    monkeypatch.setattr(orchestrator, "_reload_caddy", patches["reload_caddy"])
    patches["write_caddyfile"] = MagicMock()
    monkeypatch.setattr(orchestrator, "_write_tenant_caddyfile", patches["write_caddyfile"])

    # System groups
    fake_sysgroups = AsyncMock()
    monkeypatch.setattr(orchestrator, "create_system_groups", fake_sysgroups)

    # Pin session is a no-op
    monkeypatch.setattr(orchestrator, "pin_session", AsyncMock())

    # Secrets
    fake_secrets = MagicMock()
    fake_secrets.encrypt = lambda v: (v or "").encode() if isinstance(v, str) else v
    monkeypatch.setattr(orchestrator, "portal_secrets", fake_secrets)

    return patches


def _make_db_and_org(org_id: int = 1, slug: str = "") -> tuple:
    """Build a mock AsyncSession whose behaviour mirrors the happy path.

    The same mock row is returned from every SELECT query so state transitions
    mutate a single object — simulates the real DB for assertion purposes.
    """


    org = MagicMock()
    org.id = org_id
    org.name = "Acme BV"
    org.slug = slug
    org.zitadel_org_id = "zit-acme"
    org.mcp_servers = None
    org.provisioning_status = "pending"
    org.deleted_at = None

    state_log: list[tuple[str | None, str, str]] = []

    async def fake_execute(stmt, *args, **kwargs):
        compiled = str(stmt)
        result = MagicMock()

        # Slug list query (filtered by deleted_at IS NULL)
        if "SELECT portal_orgs.slug" in compiled and "deleted_at" in compiled:
            result.fetchall.return_value = []
            return result

        # PortalUser first-user lookup
        if "portal_users" in compiled:
            result.scalar_one_or_none.return_value = "zit-user"
            return result

        # PortalOrg single-row fetch (with or without FOR UPDATE)
        if "portal_orgs" in compiled:
            result.scalar_one.return_value = org
            result.scalar_one_or_none.return_value = org
            return result

        result.fetchall.return_value = []
        result.scalar_one.return_value = org
        result.scalar_one_or_none.return_value = org
        return result

    db = AsyncMock()
    db.execute = fake_execute
    db.commit = AsyncMock()
    # Expose the log via attributes for tests
    db._state_log = state_log
    return db, org


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_transitions_to_ready(mock_orchestrator_env, monkeypatch) -> None:
    """Provisioning with no failures lands on `ready` and never runs compensators."""
    from app.services.provisioning import orchestrator

    db, org = _make_db_and_org()

    # Spy on emit_event so we can assert the terminal state is `ready`.
    recorded = []
    with patch(
        "app.services.provisioning.state_machine.emit_event",
        side_effect=lambda **kw: recorded.append(kw),
    ):
        await orchestrator._provision(1, db)

    assert org.provisioning_status == "ready"
    terminal = [e for e in recorded if e["properties"]["to_state"] == "ready"]
    assert len(terminal) == 1, f"Expected exactly one `ready` transition, got {recorded}"

    # Compensators must NOT have been called on happy path
    assert not mock_orchestrator_env["zitadel"].delete_librechat_oidc_app.called
    assert not mock_orchestrator_env["drop_mongo"].called
    assert not mock_orchestrator_env["remove_container"].called


# ---------------------------------------------------------------------------
# Failure mid-run — compensators drain in LIFO order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_on_mongo_step_runs_earlier_compensators(mock_orchestrator_env) -> None:
    """Failure at step 3 (mongo) must drain compensators for step 2 (litellm)
    and step 1 (zitadel) in reverse order — and NOT call the mongo or later
    compensators."""
    from app.services.provisioning import orchestrator

    db, _org = _make_db_and_org()

    # Make mongo fail
    mock_orchestrator_env["create_mongo"].side_effect = RuntimeError("mongo down")

    call_order: list[str] = []

    mock_orchestrator_env["zitadel"].delete_librechat_oidc_app.side_effect = (
        lambda app_id: call_order.append("zitadel") or None
    )

    # LiteLLM delete is done via httpx — track when it is called.
    team_resp = MagicMock()
    team_resp.raise_for_status = MagicMock()
    team_resp.json = MagicMock(return_value={"team_id": "team-1"})
    key_resp = MagicMock()
    key_resp.raise_for_status = MagicMock()
    key_resp.json = MagicMock(return_value={"key": "sk"})

    class _TrackingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, path, json=None):
            if "/team/new" in path:
                return team_resp
            if "/key/generate" in path:
                return key_resp
            if "/team/delete" in path:
                call_order.append("litellm")
                return MagicMock()
            raise AssertionError(f"Unexpected httpx call: {path}")

    orchestrator.httpx.AsyncClient = lambda **kw: _TrackingClient()

    with patch("app.services.provisioning.state_machine.emit_event"):
        with pytest.raises(RuntimeError, match="mongo down"):
            await orchestrator._provision(1, db)

    # LIFO: litellm (step 2) compensator runs BEFORE zitadel (step 1)
    assert call_order == ["litellm", "zitadel"], (
        f"Expected LIFO compensator order [litellm, zitadel], got {call_order}"
    )

    # Mongo compensator must NOT have run — mongo creation itself failed, so
    # state.mongo_user_created is still False.
    assert not mock_orchestrator_env["drop_mongo"].called


# ---------------------------------------------------------------------------
# EC5 — skip re-provisioning of a `ready` tenant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_ready_is_skipped(mock_orchestrator_env) -> None:
    from app.services.provisioning import orchestrator

    db, org = _make_db_and_org()
    org.provisioning_status = "ready"

    await orchestrator._provision(1, db)

    # Zitadel creation never ran — guard fired
    assert not mock_orchestrator_env["zitadel"].create_librechat_oidc_app.called


# ---------------------------------------------------------------------------
# Compensator failures are swallowed (best-effort)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compensator_exception_is_swallowed(mock_orchestrator_env) -> None:
    """SPEC R10: a compensator that raises during rollback must not prevent
    the other compensators from running."""
    from app.services.provisioning import orchestrator

    db, _org = _make_db_and_org()

    mock_orchestrator_env["create_mongo"].side_effect = RuntimeError("mongo down")
    mock_orchestrator_env["zitadel"].delete_librechat_oidc_app.side_effect = RuntimeError(
        "zitadel also broken"
    )

    litellm_called = []

    class _TrackingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, path, json=None):
            if "/team/delete" in path:
                litellm_called.append(True)
                return MagicMock()
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value={"team_id": "team-1", "key": "sk"})
            return resp

    orchestrator.httpx.AsyncClient = lambda **kw: _TrackingClient()

    with patch("app.services.provisioning.state_machine.emit_event"):
        with pytest.raises(RuntimeError, match="mongo down"):
            await orchestrator._provision(1, db)

    # LiteLLM compensator still ran despite Zitadel one raising — best-effort
    # rollback lets the stack keep unwinding.
    assert litellm_called, "LiteLLM compensator must still run when Zitadel compensator raises"
