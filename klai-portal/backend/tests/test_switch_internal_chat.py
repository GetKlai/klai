"""The internal-chat switch rewrites only the two chat lines of a tenant .env and never prints the key.

Synthetic tenant, synthetic keys; the database, Docker and Redis are replaced.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import switch_internal_chat as switch_mod

_ENV = (
    "# Tenant: brightwater\n"
    "CREDS_KEY=feedfacefeedfacefeedfacefeedface\n"
    "LITELLM_API_KEY=sk-team-brightwater-example\n"
    "KLAI_CHAT_API_KEY=sk-team-brightwater-example\n"
    "KLAI_CHAT_BASE_URL=http://litellm:4000/v1\n"
)
_PORTAL_KEY = "pk_live_" + "b" * 40


def test_rewrite_env_replaces_only_the_named_lines():
    new = switch_mod.rewrite_env(
        _ENV, {"KLAI_CHAT_API_KEY": _PORTAL_KEY, "KLAI_CHAT_BASE_URL": switch_mod.PORTAL_CHAT_BASE_URL}
    )

    assert new == (
        "# Tenant: brightwater\n"
        "CREDS_KEY=feedfacefeedfacefeedfacefeedface\n"
        "LITELLM_API_KEY=sk-team-brightwater-example\n"
        f"KLAI_CHAT_API_KEY={_PORTAL_KEY}\n"
        "KLAI_CHAT_BASE_URL=http://portal-api:8010/partner/v1\n"
    )


@pytest.mark.parametrize(
    "values",
    [
        {"KLAI_CHAT_API_KEY": ""},  # would expand to an empty API key
        {"KLAI_CHAT_MODEL": "x"},  # not backfilled: never appended
    ],
)
def test_rewrite_env_refuses_an_empty_value_or_a_missing_line(values):
    with pytest.raises(ValueError):
        switch_mod.rewrite_env(_ENV, values)


@pytest.fixture()
def tenant(tmp_path, monkeypatch):
    env_path = tmp_path / "brightwater" / ".env"
    env_path.parent.mkdir()
    env_path.write_text(_ENV)
    env_path.chmod(0o600)
    org = SimpleNamespace(id=7, slug="brightwater", mcp_servers=None)
    db = AsyncMock()

    @asynccontextmanager
    async def _session(_org_id):
        yield db

    monkeypatch.setattr(switch_mod.settings, "librechat_container_data_path", str(tmp_path))
    monkeypatch.setattr(switch_mod, "_load_org", AsyncMock(return_value=org))
    monkeypatch.setattr(switch_mod, "tenant_scoped_session", _session)
    monkeypatch.setattr(switch_mod, "_compose_managed", lambda _slug: False)
    recreate = MagicMock()
    monkeypatch.setattr(switch_mod, "_recreate", recreate)
    return SimpleNamespace(env_path=env_path, org=org, db=db, recreate=recreate)


@pytest.mark.asyncio
async def test_switch_to_portal_mints_the_key_writes_it_and_recreates(tenant, monkeypatch, capsys):
    mint = AsyncMock(return_value=_PORTAL_KEY)
    monkeypatch.setattr(switch_mod, "holds_internal_chat_key", AsyncMock(return_value=False))
    monkeypatch.setattr(switch_mod, "mint_internal_chat_key", mint)

    assert await switch_mod.switch("brightwater", "portal") == 0

    mint.assert_awaited_once_with(tenant.db, 7, rotate=False)
    env = tenant.env_path.read_text()
    assert f"KLAI_CHAT_API_KEY={_PORTAL_KEY}\n" in env
    assert "KLAI_CHAT_BASE_URL=http://portal-api:8010/partner/v1\n" in env
    assert "LITELLM_API_KEY=sk-team-brightwater-example\n" in env
    assert tenant.env_path.stat().st_mode & 0o777 == 0o600
    tenant.recreate.assert_called_once_with(tenant.org)
    assert _PORTAL_KEY not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_switch_back_to_litellm_restores_the_team_key_and_revokes(tenant, monkeypatch):
    tenant.env_path.write_text(
        switch_mod.rewrite_env(
            _ENV, {"KLAI_CHAT_API_KEY": _PORTAL_KEY, "KLAI_CHAT_BASE_URL": switch_mod.PORTAL_CHAT_BASE_URL}
        )
    )
    revoke = AsyncMock(return_value=1)
    monkeypatch.setattr(switch_mod, "revoke_internal_chat_key", revoke)

    assert await switch_mod.switch("brightwater", "litellm") == 0

    assert tenant.env_path.read_text() == _ENV
    tenant.recreate.assert_called_once_with(tenant.org)
    revoke.assert_awaited_once_with(tenant.db, 7)


def test_write_env_is_owner_only_from_the_first_byte_and_leaves_no_temp_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("A=1\n")
    env_path.chmod(0o644)

    switch_mod._write_env(env_path, "A=2\n")

    assert env_path.read_text() == "A=2\n"
    assert env_path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".env.switch").exists()


@pytest.mark.asyncio
async def test_switch_back_of_a_compose_tenant_keeps_the_key_until_the_recreate(tenant, monkeypatch, capsys):
    tenant.env_path.write_text(
        switch_mod.rewrite_env(
            _ENV, {"KLAI_CHAT_API_KEY": _PORTAL_KEY, "KLAI_CHAT_BASE_URL": switch_mod.PORTAL_CHAT_BASE_URL}
        )
    )
    monkeypatch.setattr(switch_mod, "_compose_managed", lambda _slug: True)
    monkeypatch.setattr(
        switch_mod,
        "validate_slug_for_provisioning",
        lambda slug, domain: SimpleNamespace(librechat_container=f"librechat-{slug}"),
    )
    revoke = AsyncMock(return_value=1)
    monkeypatch.setattr(switch_mod, "revoke_internal_chat_key", revoke)

    assert await switch_mod.switch("brightwater", "litellm") == switch_mod.EXIT_COMPOSE_RECREATE_PENDING

    revoke.assert_not_awaited()
    tenant.recreate.assert_not_called()
    assert "brightwater revoke" in capsys.readouterr().out

    assert await switch_mod.revoke("brightwater") == 0
    revoke.assert_awaited_once_with(tenant.db, 7)
