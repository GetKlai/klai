"""Tests for klai_pii_org_policy.py (SPEC-PRIVACY-MISTRAL-PII-001 REQ-7 /
NFR "Tenant isolation")."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from klai_pii_entities import RETURN_SET_ENTITIES
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

    policy = (await mod.resolve_org_pii_context(None)).entities
    assert policy == frozenset()
    assert client.calls == []


@pytest.mark.asyncio
async def test_enabled_entities_returned_and_filtered_to_return_set(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["IBAN_CODE", "PHONE_NUMBER"]})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = (await mod.resolve_org_pii_context("org-123")).entities
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

    policy = (await mod.resolve_org_pii_context("org-123")).entities
    assert "PERSON" not in policy
    assert policy == frozenset({"IBAN_CODE"})


@pytest.mark.asyncio
async def test_unknown_entity_strings_are_dropped(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["IBAN_CODE", "NOT_A_REAL_ENTITY"]})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = (await mod.resolve_org_pii_context("org-123")).entities
    assert policy == frozenset({"IBAN_CODE"})


# These four pinned "any failure resolves to the empty policy". That WAS the
# safe answer while REQ-7's default was off; since D2 made the return set
# default-on it is the unsafe one — an empty policy now means seven entity
# types travel to Mistral unmasked. They pin the replacement contract instead
# (``_degraded``): the tenant's own last policy when there is one, the platform
# default when there is not. The failure inputs are unchanged.
@pytest.mark.asyncio
async def test_network_error_degrades_rather_than_unmasking(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(raise_exc=ConnectionError("portal-api unreachable"))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = (await mod.resolve_org_pii_context("org-123")).entities
    assert policy == RETURN_SET_ENTITIES


@pytest.mark.asyncio
async def test_non_2xx_response_degrades_rather_than_unmasking(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({}, status_ok=False))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = (await mod.resolve_org_pii_context("org-123")).entities
    assert policy == RETURN_SET_ENTITIES


@pytest.mark.asyncio
async def test_malformed_payload_degrades_rather_than_unmasking(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({"unexpected": "shape"}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    policy = (await mod.resolve_org_pii_context("org-123")).entities
    assert policy == RETURN_SET_ENTITIES


@pytest.mark.asyncio
async def test_missing_secret_degrades_without_a_network_call(monkeypatch):
    mod = _load_policy_module(monkeypatch, extra_env={"PORTAL_INTERNAL_SECRET": ""})
    client = _FakeAsyncClient(response=_FakeResponse({"enabled_entities": ["IBAN_CODE"]}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    context = await mod.resolve_org_pii_context("org-123")
    assert context.entities == RETURN_SET_ENTITIES
    # A misconfigured secret must never make us talk about a tenant either.
    assert context.telemetry_level == "off"
    assert client.calls == []


@pytest.mark.asyncio
async def test_result_is_cached_per_org(monkeypatch):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({"enabled_entities": ["IBAN_CODE"]}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    await mod.resolve_org_pii_context("org-123")
    await mod.resolve_org_pii_context("org-123")
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

    policy_a = (await mod.resolve_org_pii_context("org-a")).entities
    policy_b = (await mod.resolve_org_pii_context("org-b")).entities
    assert policy_a == frozenset({"IBAN_CODE"})
    assert policy_b == frozenset({"PHONE_NUMBER"})
    assert policy_a != policy_b


# ---------------------------------------------------------------------------
# telemetry_level — resolved alongside the entity policy, fail-closed to off
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["off", "shadow", "full"])
async def test_telemetry_level_is_returned_from_the_payload(monkeypatch, level):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["IBAN_CODE"], "telemetry_level": level})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    context = await mod.resolve_org_pii_context("org-123")

    assert context.telemetry_level == level
    assert context.entities == frozenset({"IBAN_CODE"})


@pytest.mark.asyncio
async def test_absent_telemetry_level_falls_back_to_off(monkeypatch):
    """A portal-api old enough not to send the field has authorised nothing.

    Defaulting to ``shadow`` here would start emitting telemetry about every
    tenant during a rolling deploy, on the strength of a field that was never
    sent. Silence is the only safe reading of an absent value.
    """
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({"enabled_entities": ["IBAN_CODE"]}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    context = await mod.resolve_org_pii_context("org-123")

    assert context.telemetry_level == "off"
    assert context.entities == frozenset({"IBAN_CODE"})


@pytest.mark.asyncio
@pytest.mark.parametrize("bogus", ["verbose", "", None, 3, "OFF"])
async def test_unrecognised_telemetry_level_falls_back_to_off(monkeypatch, bogus):
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["IBAN_CODE"], "telemetry_level": bogus})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    assert (await mod.resolve_org_pii_context("org-123")).telemetry_level == "off"


@pytest.mark.asyncio
async def test_missing_org_id_yields_the_silent_empty_context(monkeypatch):
    mod = _load_policy_module(monkeypatch)

    context = await mod.resolve_org_pii_context(None)

    assert context == mod.EMPTY_CONTEXT
    assert context.telemetry_level == "off"
    assert context.entities == frozenset()


# ---------------------------------------------------------------------------
# Degraded resolution — a portal-api outage must not silently unmask
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_failure_serves_the_last_known_good_policy(monkeypatch):
    """Under default-on, EMPTY_CONTEXT on failure means masking silently STOPS.

    That was harmless while the documented default was off. Since D2 it is the
    larger of the two mistakes: LiteLLM keeps serving chat while portal-api is
    unreachable, so the seven entity types would flow to Mistral unmasked at
    exactly the moment the control matters.
    """
    mod = _load_policy_module(monkeypatch)

    good = _FakeAsyncClient(
        response=_FakeResponse(
            {"enabled_entities": ["IBAN_CODE", "PHONE_NUMBER"], "telemetry_level": "shadow"}
        )
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: good))
    first = await mod.resolve_org_pii_context("org-123")
    assert first.entities == frozenset({"IBAN_CODE", "PHONE_NUMBER"})

    # TTL expires, portal-api is now down.
    monkeypatch.setattr(mod, "_POLICY_CACHE_TTL_SECONDS", -1.0)
    down = _FakeAsyncClient(raise_exc=RuntimeError("connection refused"))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: down))

    degraded = await mod.resolve_org_pii_context("org-123")

    assert degraded.entities == frozenset({"IBAN_CODE", "PHONE_NUMBER"})
    assert degraded.telemetry_level == "shadow"


@pytest.mark.asyncio
async def test_degraded_honours_an_opt_out_instead_of_re_enabling_it(monkeypatch):
    """Last-good, not "fall back to the full default set".

    A tenant that deliberately switched everything off must not have masking
    switched back on by an outage — that would override a controller decision
    with an infrastructure event.
    """
    mod = _load_policy_module(monkeypatch)

    opted_out = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": [], "telemetry_level": "off"})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: opted_out))
    await mod.resolve_org_pii_context("org-123")

    monkeypatch.setattr(mod, "_POLICY_CACHE_TTL_SECONDS", -1.0)
    down = _FakeAsyncClient(raise_exc=RuntimeError("boom"))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: down))

    degraded = await mod.resolve_org_pii_context("org-123")

    assert degraded.entities == frozenset()
    assert degraded.telemetry_level == "off"


@pytest.mark.asyncio
async def test_a_cold_worker_during_an_outage_masks_the_full_default_set(monkeypatch):
    """The case last-good alone does not cover.

    A LiteLLM restart (deploy, crash) while portal-api is unreachable leaves
    every tenant cold. Resolving those to the empty policy would send the whole
    return set to Mistral unmasked, correlated with exactly the kind of
    incident that causes both at once.
    """
    mod = _load_policy_module(monkeypatch)
    down = _FakeAsyncClient(raise_exc=RuntimeError("connection refused"))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: down))

    context = await mod.resolve_org_pii_context("org-never-seen")

    assert context.entities == RETURN_SET_ENTITIES
    # Masking more than a tenant asked for is recoverable; talking about a
    # tenant who never consented is not.
    assert context.telemetry_level == "off"


@pytest.mark.asyncio
async def test_a_missing_org_id_still_yields_the_empty_context(monkeypatch):
    """No identity means no tenant to mask for and none to be silent about."""
    mod = _load_policy_module(monkeypatch)
    client = _FakeAsyncClient(response=_FakeResponse({"enabled_entities": ["IBAN_CODE"]}))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    context = await mod.resolve_org_pii_context(None)

    assert context == mod.EMPTY_CONTEXT
    assert client.calls == []


@pytest.mark.asyncio
async def test_degraded_does_not_leak_across_orgs(monkeypatch):
    """Serving "the last good policy" must mean THIS org's, not the last one seen."""
    mod = _load_policy_module(monkeypatch)
    good = _FakeAsyncClient(
        response=_FakeResponse({"enabled_entities": ["IBAN_CODE"], "telemetry_level": "full"})
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: good))
    await mod.resolve_org_pii_context("org-a")

    down = _FakeAsyncClient(raise_exc=RuntimeError("boom"))
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: down))
    other = await mod.resolve_org_pii_context("org-b")

    # The cold default, not org-a's narrower set and not org-a's `full`.
    assert other.entities == RETURN_SET_ENTITIES
    assert other.telemetry_level == "off"
