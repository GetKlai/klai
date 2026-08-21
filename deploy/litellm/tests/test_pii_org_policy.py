"""Tests for klai_pii_org_policy.py (SPEC-PRIVACY-MISTRAL-PII-001 REQ-7 /
NFR "Tenant isolation")."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


def _load_policy_module(monkeypatch, extra_env=None):
    env = {"PORTAL_INTERNAL_SECRET": "test-secret", "PORTAL_API_URL": "http://portal-api:8000"}
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_klai_kb_modules()
    import klai_pii_org_policy

    return klai_pii_org_policy


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("simulated non-2xx")

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None, **kwargs):
        self.calls.append({"url": url, "headers": headers})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


@pytest.mark.asyncio
async def test_missing_org_id_fails_closed_no_call(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({"enabled_entities": ["IBAN_CODE"]}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy(None)
    assert policy == frozenset()
    assert client.calls == []


@pytest.mark.asyncio
async def test_enabled_entities_returned_and_filtered_to_return_set(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["IBAN_CODE", "PHONE_NUMBER"]})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy("org-123")
    assert policy == frozenset({"IBAN_CODE", "PHONE_NUMBER"})


@pytest.mark.asyncio
async def test_person_in_response_is_dropped_structurally(monkeypatch):
    """Even a crafted/buggy portal-api response claiming PERSON is enabled
    cannot make PERSON survive this function -- it is intersected against
    RETURN_SET_ENTITIES, which never contains PERSON."""
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["PERSON", "IBAN_CODE"]})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy("org-123")
    assert "PERSON" not in policy
    assert policy == frozenset({"IBAN_CODE"})


@pytest.mark.asyncio
async def test_unknown_entity_strings_are_dropped(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["IBAN_CODE", "NOT_A_REAL_ENTITY"]})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy("org-123")
    assert policy == frozenset({"IBAN_CODE"})


@pytest.mark.asyncio
async def test_network_error_fails_closed(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(raise_exc=ConnectionError("portal-api unreachable"))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy("org-123")
    assert policy == frozenset()


@pytest.mark.asyncio
async def test_non_2xx_response_fails_closed(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({}, status_ok=False))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy("org-123")
    assert policy == frozenset()


@pytest.mark.asyncio
async def test_malformed_payload_fails_closed(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({"unexpected": "shape"}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy("org-123")
    assert policy == frozenset()


@pytest.mark.asyncio
async def test_missing_secret_fails_closed_without_a_network_call(monkeypatch):
    mod = _load_policy_module(monkeypatch, extra_env={"PORTAL_INTERNAL_SECRET": ""})
    client = _FakeAsyncClient(response=_FakeResponse({"enabled_entities": ["IBAN_CODE"]}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = await mod.resolve_org_entity_policy("org-123")
    assert policy == frozenset()
    assert client.calls == []


@pytest.mark.asyncio
async def test_result_is_cached_per_org(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({"enabled_entities": ["IBAN_CODE"]}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    await mod.resolve_org_entity_policy("org-123")
    await mod.resolve_org_entity_policy("org-123")
    assert len(client.calls) == 1  # second call served from cache


@pytest.mark.asyncio
async def test_cache_is_scoped_per_org_not_shared(monkeypatch):
    """Tenant-isolation guard: org A's cached policy must never answer for
    org B."""
    mod = _load_policy_module(monkeypatch)

    class _MultiOrgClient(_FakeAsyncClient):
        async def get(self, url, headers=None, **kwargs):
            self.calls.append({"url": url, "headers": headers})
            if "/orgs/org-a/" in url:
                return _FakeResponse({"enabled_entities": ["IBAN_CODE"]})
            return _FakeResponse({"enabled_entities": ["PHONE_NUMBER"]})

    client = _MultiOrgClient()
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy_a = await mod.resolve_org_entity_policy("org-a")
    policy_b = await mod.resolve_org_entity_policy("org-b")
    assert policy_a == frozenset({"IBAN_CODE"})
    assert policy_b == frozenset({"PHONE_NUMBER"})
    assert policy_a != policy_b
