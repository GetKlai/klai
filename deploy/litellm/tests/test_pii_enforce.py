"""Tests for klai_pii_enforce.py (SPEC-PRIVACY-MISTRAL-PII-001 Phase 3,
REQ-7 through REQ-11).

litellm is not installed as the real proxy package for these tests (see
test_klai_knowledge_hook.py / test_pii_observe.py's same rationale) — the
import is mocked so KlaiPiiEnforcer's CustomLogger base class resolves.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


@pytest.fixture(autouse=True)
def _mock_litellm():
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    litellm_mod.integrations = integrations_mod
    integrations_mod.custom_logger = custom_logger_mod

    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod

    yield

    for mod_name in ["litellm", "litellm.integrations", "litellm.integrations.custom_logger"]:
        sys.modules.pop(mod_name, None)
    reset_klai_kb_modules()


def _load_enforcer(monkeypatch, *, enforce=True, extra_env=None):
    env = {
        "KLAI_PII_ENFORCE": "true" if enforce else "false",
        "PRESIDIO_ANALYZER_API_BASE": "http://presidio-analyzer:3000",
        "PORTAL_INTERNAL_SECRET": "test-secret",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_klai_kb_modules()
    import klai_pii_enforce

    return klai_pii_enforce


def _user_api_key(org_id=None):
    uak = MagicMock()
    uak.metadata = {"org_id": org_id} if org_id is not None else {}
    return uak


def _stub_org_policy(
    mod,
    monkeypatch,
    policy: frozenset[str] | dict[str, frozenset[str]],
    telemetry_level: str = "shadow",
):
    """Replace resolve_org_pii_context with a deterministic stub.

    ``policy`` may be a single frozenset (same answer for every org) or a
    dict keyed by org_id for multi-org (cross-tenant) tests.

    ``telemetry_level`` defaults to ``shadow`` — the production default for a
    tenant that has not changed it — so the existing masking tests exercise
    the path where telemetry IS emitted, and the emitter cannot silently break
    without a test noticing.
    """
    from klai_pii_org_policy import OrgPiiContext

    async def _resolve(org_id):
        entities = policy.get(org_id, frozenset()) if isinstance(policy, dict) else policy
        return OrgPiiContext(entities=entities, telemetry_level=telemetry_level)

    monkeypatch.setattr(mod, "resolve_org_pii_context", _resolve)


# ---------------------------------------------------------------------------
# Analyzer fakes
# ---------------------------------------------------------------------------
class _FakeAnalyzerResponse:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return self._results


class _ScriptedAnalyzerClient:
    """Maps exact input text -> a canned /analyze results list."""

    def __init__(self, script=None, default=None, raise_exc=None):
        self.script = script or {}
        self.default = default if default is not None else []
        self.raise_exc = raise_exc
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json})
        if self.raise_exc is not None:
            raise self.raise_exc
        text = json["text"]
        return _FakeAnalyzerResponse(self.script.get(text, self.default))


def _install_analyzer(mod, monkeypatch, client):
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))


# ---------------------------------------------------------------------------
# Streaming/response object helpers (litellm response shapes, dict-free)
# ---------------------------------------------------------------------------
def _chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


async def _achunks(*contents):
    for c in contents:
        yield _chunk(c)


def _response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


async def _drain(agen):
    return [item async for item in agen]


def _all_content(chunks):
    return "".join(
        c.choices[0].delta.content or "" for c in chunks if c.choices and c.choices[0].delta.content
    )


# ===========================================================================
# 1. Flag OFF: byte-identical passthrough
# ===========================================================================
@pytest.mark.asyncio
async def test_flag_off_pre_call_returns_identical_object_no_analyzer_call(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=False)
    client = _ScriptedAnalyzerClient(raise_exc=AssertionError("must not call analyzer when off"))
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-off-1",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    original_identity = data

    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )

    assert result is original_identity
    assert result == {
        "model": "klai-primary",
        "litellm_call_id": "call-off-1",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    assert client.calls == []
    assert mod._pii_map_store.get("call-off-1") is None


@pytest.mark.asyncio
async def test_flag_off_post_call_success_hook_returns_none(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=False)
    result = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": "call-off-2"}, _user_api_key("org1"), _response("hello")
    )
    assert result is None


@pytest.mark.asyncio
async def test_flag_off_streaming_hook_passes_chunks_through_unchanged(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=False)
    chunks = await _drain(
        mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
            _user_api_key("org1"), _achunks("hello ", "world"), {"litellm_call_id": "call-off-3"}
        )
    )
    assert _all_content(chunks) == "hello world"


@pytest.mark.asyncio
async def test_flag_off_failure_hook_returns_none_and_touches_nothing(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=False)
    mod._pii_map_store.put("call-off-4", {"<PERSON_1>": "Jan de Vries"})
    result = await mod.klai_pii_enforcer.async_post_call_failure_hook(
        {"litellm_call_id": "call-off-4"}, RuntimeError("boom"), _user_api_key("org1")
    )
    assert result is None
    # Flag is off -> the failure hook must not even touch the store.
    assert mod._pii_map_store.get("call-off-4") == {"<PERSON_1>": "Jan de Vries"}


# ===========================================================================
# 2. Flag ON: BSN masked, never restored
# ===========================================================================
@pytest.mark.asyncio
async def test_bsn_masked_and_not_restored(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset())  # no optional entities

    text = "mijn bsn is 111222333 graag verwerken"
    start, end = text.index("111222333"), text.index("111222333") + len("111222333")
    client = _ScriptedAnalyzerClient(
        script={text: [{"entity_type": "NL_BSN", "start": start, "end": end, "score": 0.85}]}
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-bsn-1",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )

    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert "<NL_BSN_1>" in user_message["content"]
    assert "111222333" not in user_message["content"]
    # Nothing restorable was masked (NL_BSN is never-restore), so no map
    # entry is created at all -- there is nothing to restore, structurally.
    assert mod._pii_map_store.get("call-bsn-1") is None

    # Even if the model somehow echoes the placeholder, restore must not
    # bring the real BSN back. No restore map entry exists at all for a
    # call that only masked never-restore entities, so the hook returns
    # None (CustomLogger's "no change" contract) -- the ORIGINAL response
    # object is what the proxy keeps, and it still carries the literal,
    # unrestored placeholder.
    response = _response("Genoteerd: <NL_BSN_1>")
    hook_return = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": "call-bsn-1"}, _user_api_key("org1"), response
    )
    assert hook_return is None
    assert "111222333" not in response.choices[0].message.content
    assert "<NL_BSN_1>" in response.choices[0].message.content


# ===========================================================================
# 3. Flag ON: SECRET masked, never restored
# ===========================================================================
@pytest.mark.asyncio
async def test_secret_masked_and_not_restored(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset())

    secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
    text = f"gebruik deze key: {secret} in de config"
    start, end = text.index(secret), text.index(secret) + len(secret)
    client = _ScriptedAnalyzerClient(
        script={text: [{"entity_type": "SECRET", "start": start, "end": end, "score": 0.95}]}
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-secret-1",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert "<SECRET_1>" in user_message["content"]
    assert secret not in user_message["content"]
    assert mod._pii_map_store.get("call-secret-1") is None

    response = _response("Ik zie dat je <SECRET_1> hebt gedeeld.")
    hook_return = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": "call-secret-1"}, _user_api_key("org1"), response
    )
    assert hook_return is None
    assert secret not in response.choices[0].message.content
    assert "<SECRET_1>" in response.choices[0].message.content


# ===========================================================================
# 4/5. Per-org REQ-7 policy: enabled -> masked+restored; disabled -> untouched
# ===========================================================================
@pytest.mark.asyncio
async def test_iban_masked_and_restored_when_enabled_for_org(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}))

    iban = "NL91ABNA0417164300"
    text = f"Betaal op IBAN {iban} graag."
    start, end = text.index(iban), text.index(iban) + len(iban)
    client = _ScriptedAnalyzerClient(
        script={text: [{"entity_type": "IBAN_CODE", "start": start, "end": end, "score": 1.0}]}
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-iban-1",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert "<IBAN_CODE_1>" in user_message["content"]
    assert iban not in user_message["content"]
    assert mod._pii_map_store.get("call-iban-1") == {"<IBAN_CODE_1>": iban}

    response = _response("Bevestigd, over te maken naar <IBAN_CODE_1>.")
    restored = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": "call-iban-1"}, _user_api_key("org1"), response
    )
    assert iban in restored.choices[0].message.content
    assert "<IBAN_CODE_1>" not in restored.choices[0].message.content


@pytest.mark.asyncio
async def test_iban_untouched_when_not_in_org_policy(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset())  # IBAN_CODE not enabled

    iban = "NL91ABNA0417164300"
    text = f"Betaal op IBAN {iban} graag."
    start, end = text.index(iban), text.index(iban) + len(iban)
    client = _ScriptedAnalyzerClient(
        script={text: [{"entity_type": "IBAN_CODE", "start": start, "end": end, "score": 1.0}]}
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-iban-2",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert user_message["content"] == text  # fully untouched
    assert len(result["messages"]) == 1  # no verbatim-instruction message injected
    assert mod._pii_map_store.get("call-iban-2") is None


# ===========================================================================
# 6. REQ-8 numbering: two occurrences of the same entity type in one
# request must not collapse into one token, and each restores independently.
# (Uses PHONE_NUMBER, not PERSON: PERSON is structurally excluded from
# effective_enabled_entities regardless of org policy — see
# klai_pii_entities.py / REQ-9. The identical numbering mechanism is
# exercised directly against PERSON, unconstrained by org policy, in
# tests/test_pii_text_masking.py.)
# ===========================================================================
@pytest.mark.asyncio
async def test_two_distinct_phone_numbers_get_distinct_placeholders_and_restore(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset({"PHONE_NUMBER"}))

    phone_a, phone_b = "06-12345678", "06-98765432"
    text = f"Bel {phone_a} of anders {phone_b}."
    a_start = text.index(phone_a)
    b_start = text.index(phone_b)
    client = _ScriptedAnalyzerClient(
        script={
            text: [
                {
                    "entity_type": "PHONE_NUMBER",
                    "start": a_start,
                    "end": a_start + len(phone_a),
                    "score": 0.9,
                },
                {
                    "entity_type": "PHONE_NUMBER",
                    "start": b_start,
                    "end": b_start + len(phone_b),
                    "score": 0.9,
                },
            ]
        }
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-two-phones",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert "<PHONE_NUMBER_1>" in user_message["content"]
    assert "<PHONE_NUMBER_2>" in user_message["content"]

    restore_map = mod._pii_map_store.get("call-two-phones")
    assert restore_map == {"<PHONE_NUMBER_1>": phone_a, "<PHONE_NUMBER_2>": phone_b}

    response = _response(f"Genoteerd: <PHONE_NUMBER_1> en <PHONE_NUMBER_2>.")
    restored = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": "call-two-phones"}, _user_api_key("org1"), response
    )
    restored_text = restored.choices[0].message.content
    assert phone_a in restored_text
    assert phone_b in restored_text
    # Neither placeholder resolved to the wrong number.
    assert restored_text.index(phone_a) < restored_text.index(phone_b)


# ===========================================================================
# 7. REQ-8 chunk-boundary safety through the real streaming hook
# ===========================================================================
@pytest.mark.asyncio
async def test_placeholder_split_across_chunk_boundary_via_streaming_hook(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    call_id = "call-stream-split"
    mod._pii_map_store.put(call_id, {"<PERSON_1>": "Jan de Vries"})

    chunks = await _drain(
        mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
            _user_api_key("org1"),
            _achunks("Hallo <PERS", "ON_1> vriendelijke groet"),
            {"litellm_call_id": call_id},
        )
    )
    full_text = _all_content(chunks)
    assert full_text == "Hallo Jan de Vries vriendelijke groet"
    # No individual chunk ever carries a bare, unrestored partial fragment.
    for c in chunks:
        content = c.choices[0].delta.content or ""
        assert "<PERS" not in content or "<PERSON_1>" not in content and "PERSON_1" not in content
    # Map entry cleaned up at stream end.
    assert mod._pii_map_store.get(call_id) is None


# ===========================================================================
# 8. Streaming AND non-streaming both restore (combined smoke check)
# ===========================================================================
@pytest.mark.asyncio
async def test_streaming_and_non_streaming_both_restore_return_set_entities(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)

    # Non-streaming
    call_id_ns = "call-both-ns"
    mod._pii_map_store.put(call_id_ns, {"<EMAIL_ADDRESS_1>": "jan@example.nl"})
    restored = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": call_id_ns}, _user_api_key("org1"), _response("mail: <EMAIL_ADDRESS_1>")
    )
    assert "jan@example.nl" in restored.choices[0].message.content

    # Streaming
    call_id_s = "call-both-s"
    mod._pii_map_store.put(call_id_s, {"<EMAIL_ADDRESS_1>": "jan@example.nl"})
    chunks = await _drain(
        mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
            _user_api_key("org1"), _achunks("mail: ", "<EMAIL_ADDRESS_1>"), {"litellm_call_id": call_id_s}
        )
    )
    assert "jan@example.nl" in _all_content(chunks)


# ===========================================================================
# 9. Cross-tenant isolation — the most important test (REQ-11)
# ===========================================================================
@pytest.mark.asyncio
async def test_two_concurrent_requests_different_orgs_do_not_cross_contaminate(monkeypatch):
    mod = _load_enforcer(
        monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org-a,org-b"}
    )
    _stub_org_policy(
        mod,
        monkeypatch,
        {"org-a": frozenset({"PHONE_NUMBER"}), "org-b": frozenset({"PHONE_NUMBER"})},
    )

    phone_a = "06-11111111"
    phone_b = "06-22222222"
    text_a = f"Bel {phone_a} alstublieft."
    text_b = f"Bel {phone_b} alstublieft."

    client = _ScriptedAnalyzerClient(
        script={
            text_a: [
                {
                    "entity_type": "PHONE_NUMBER",
                    "start": text_a.index(phone_a),
                    "end": text_a.index(phone_a) + len(phone_a),
                    "score": 0.9,
                }
            ],
            text_b: [
                {
                    "entity_type": "PHONE_NUMBER",
                    "start": text_b.index(phone_b),
                    "end": text_b.index(phone_b) + len(phone_b),
                    "score": 0.9,
                }
            ],
        }
    )
    _install_analyzer(mod, monkeypatch, client)

    data_a = {
        "model": "klai-primary",
        "litellm_call_id": "call-org-a",
        "messages": [{"role": "user", "content": text_a}],
    }
    data_b = {
        "model": "klai-primary",
        "litellm_call_id": "call-org-b",
        "messages": [{"role": "user", "content": text_b}],
    }

    # Run both pre-call masks CONCURRENTLY -- this is the scenario REQ-11
    # exists to make safe: two in-flight requests racing on the same
    # process-local store.
    result_a, result_b = await asyncio.gather(
        mod.klai_pii_enforcer.async_pre_call_hook(_user_api_key("org-a"), None, data_a, "completion"),
        mod.klai_pii_enforcer.async_pre_call_hook(_user_api_key("org-b"), None, data_b, "completion"),
    )

    user_a = [m for m in result_a["messages"] if m.get("role") == "user"][0]["content"]
    user_b = [m for m in result_b["messages"] if m.get("role") == "user"][0]["content"]
    assert phone_b not in user_a
    assert phone_a not in user_b

    # Now run both restores CONCURRENTLY too.
    response_a = _response("Genoteerd: <PHONE_NUMBER_1>")
    response_b = _response("Genoteerd: <PHONE_NUMBER_1>")  # same placeholder text, different maps
    restored_a, restored_b = await asyncio.gather(
        mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": "call-org-a"}, _user_api_key("org-a"), response_a
        ),
        mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": "call-org-b"}, _user_api_key("org-b"), response_b
        ),
    )

    text_out_a = restored_a.choices[0].message.content
    text_out_b = restored_b.choices[0].message.content
    assert phone_a in text_out_a
    assert phone_b not in text_out_a
    assert phone_b in text_out_b
    assert phone_a not in text_out_b


@pytest.mark.asyncio
async def test_two_concurrent_streaming_requests_different_orgs_do_not_cross_contaminate(
    monkeypatch,
):
    mod = _load_enforcer(monkeypatch, enforce=True)
    mod._pii_map_store.put("call-stream-org-a", {"<PERSON_1>": "Jan de Vries"})
    mod._pii_map_store.put("call-stream-org-b", {"<PERSON_1>": "Marieke Bakker"})

    async def _run(call_id):
        chunks = await _drain(
            mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
                _user_api_key("org"), _achunks("Groeten aan <PERSON_1>."), {"litellm_call_id": call_id}
            )
        )
        return _all_content(chunks)

    text_a, text_b = await asyncio.gather(_run("call-stream-org-a"), _run("call-stream-org-b"))
    assert "Jan de Vries" in text_a
    assert "Marieke Bakker" not in text_a
    assert "Marieke Bakker" in text_b
    assert "Jan de Vries" not in text_b


# ===========================================================================
# 10. Map lifecycle through the hooks
# ===========================================================================
@pytest.mark.asyncio
async def test_map_entry_deleted_after_stream_end(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    call_id = "call-lifecycle-1"
    mod._pii_map_store.put(call_id, {"<PERSON_1>": "Jan de Vries"})
    await _drain(
        mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
            _user_api_key("org1"), _achunks("Groeten aan <PERSON_1>."), {"litellm_call_id": call_id}
        )
    )
    assert mod._pii_map_store.get(call_id) is None


@pytest.mark.asyncio
async def test_map_entry_deleted_after_error_mid_stream(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    call_id = "call-lifecycle-2"
    mod._pii_map_store.put(call_id, {"<PERSON_1>": "Jan de Vries"})

    async def _broken_stream():
        yield _chunk("Groeten aan ")
        raise ConnectionError("upstream dropped mid-stream")

    with pytest.raises(ConnectionError):
        await _drain(
            mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
                _user_api_key("org1"), _broken_stream(), {"litellm_call_id": call_id}
            )
        )
    assert mod._pii_map_store.get(call_id) is None


@pytest.mark.asyncio
async def test_map_entry_deleted_via_failure_hook(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    call_id = "call-lifecycle-3"
    mod._pii_map_store.put(call_id, {"<PERSON_1>": "Jan de Vries"})
    await mod.klai_pii_enforcer.async_post_call_failure_hook(
        {"litellm_call_id": call_id}, RuntimeError("boom"), _user_api_key("org1")
    )
    assert mod._pii_map_store.get(call_id) is None


@pytest.mark.asyncio
async def test_map_entry_deleted_after_success_hook_even_with_no_restore_map(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    call_id = "call-lifecycle-4"
    # No put() -- nothing was masked for this call.
    result = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": call_id}, _user_api_key("org1"), _response("hello")
    )
    assert result is None
    assert mod._pii_map_store.get(call_id) is None


# ===========================================================================
# 11. REQ-10: analyzer failure behaviour
# ===========================================================================
@pytest.mark.asyncio
async def test_analyzer_error_with_enforcement_on_fails_the_request(monkeypatch, caplog):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset())
    client = _ScriptedAnalyzerClient(raise_exc=ConnectionError("presidio-analyzer unreachable"))
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-analyzer-down",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    with caplog.at_level("WARNING", logger="klai_pii_enforce"):
        with pytest.raises(mod.PiiAnalyzerUnavailable):
            await mod.klai_pii_enforcer.async_pre_call_hook(
                _user_api_key("org1"), None, data, "completion"
            )
    assert any("pii_enforce_analyzer_failed" in r.message for r in caplog.records)
    assert any("org1" in r.message for r in caplog.records)
    # No unminimised payload was ever recorded as maskable.
    assert mod._pii_map_store.get("call-analyzer-down") is None


@pytest.mark.asyncio
async def test_analyzer_error_with_enforcement_off_does_nothing(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=False)
    client = _ScriptedAnalyzerClient(raise_exc=ConnectionError("presidio-analyzer unreachable"))
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-analyzer-down-2",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    # Must NOT raise -- enforcement is off, the analyzer is never called.
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    assert result is data
    assert client.calls == []


# ===========================================================================
# 12. Verbatim-token instruction (REQ-0b) present exactly when masking fires
# ===========================================================================
@pytest.mark.asyncio
async def test_verbatim_instruction_injected_when_masking_active(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset())

    text = "mijn bsn is 111222333 graag verwerken"
    start, end = text.index("111222333"), text.index("111222333") + len("111222333")
    client = _ScriptedAnalyzerClient(
        script={text: [{"entity_type": "NL_BSN", "start": start, "end": end, "score": 0.85}]}
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-instr-1",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    from klai_pii_restore_eval import VERBATIM_TOKEN_SYSTEM_INSTRUCTION

    assert any(
        m.get("content") == VERBATIM_TOKEN_SYSTEM_INSTRUCTION for m in result["messages"]
    )


@pytest.mark.asyncio
async def test_verbatim_instruction_absent_when_nothing_is_masked(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset())

    text = "hallo, hoe gaat het vandaag met je?"
    client = _ScriptedAnalyzerClient(script={text: []})  # nothing detected
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-instr-2",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    from klai_pii_restore_eval import VERBATIM_TOKEN_SYSTEM_INSTRUCTION

    assert result["messages"] == [{"role": "user", "content": text}]
    assert not any(
        m.get("content") == VERBATIM_TOKEN_SYSTEM_INSTRUCTION for m in result["messages"]
    )


# ===========================================================================
# Agentic path: tool_calls[].function.arguments is masked too
# ===========================================================================
@pytest.mark.asyncio
async def test_tool_call_arguments_are_masked(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"})
    _stub_org_policy(mod, monkeypatch, frozenset({"EMAIL_ADDRESS"}))

    args_text = '{"to": "jan.devries@example.nl"}'
    email = "jan.devries@example.nl"
    start, end = args_text.index(email), args_text.index(email) + len(email)
    client = _ScriptedAnalyzerClient(
        script={
            args_text: [
                {"entity_type": "EMAIL_ADDRESS", "start": start, "end": end, "score": 0.9}
            ]
        }
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-large",
        "litellm_call_id": "call-toolcall-1",
        "messages": [
            {"role": "user", "content": "stuur dit door"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"function": {"name": "send_mail", "arguments": args_text}}],
            },
        ],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    assistant_msg = [m for m in result["messages"] if m.get("role") == "assistant"][0]
    masked_args = assistant_msg["tool_calls"][0]["function"]["arguments"]
    assert email not in masked_args
    assert "<EMAIL_ADDRESS_1>" in masked_args


# ===========================================================================
# Activation hardening — KLAI_PII_ENFORCE_ORG_IDS per-org scoping
# ===========================================================================
@pytest.mark.asyncio
async def test_org_not_in_allowlist_is_not_enforced(monkeypatch):
    mod = _load_enforcer(
        monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org-other"}
    )
    client = _ScriptedAnalyzerClient(raise_exc=AssertionError("must not call analyzer"))
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-allowlist-1",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    assert result is data
    assert client.calls == []
    assert mod._pii_map_store.get("call-allowlist-1") is None


@pytest.mark.asyncio
async def test_org_in_allowlist_is_enforced(monkeypatch):
    mod = _load_enforcer(
        monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1,org2"}
    )
    _stub_org_policy(mod, monkeypatch, frozenset())

    text = "mijn bsn is 111222333 graag verwerken"
    start, end = text.index("111222333"), text.index("111222333") + len("111222333")
    client = _ScriptedAnalyzerClient(
        script={text: [{"entity_type": "NL_BSN", "start": start, "end": end, "score": 0.85}]}
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-allowlist-2",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert "<NL_BSN_1>" in user_message["content"]
    assert client.calls  # analyzer WAS called for the allowlisted org


@pytest.mark.asyncio
async def test_empty_allowlist_means_enforcement_for_no_org_even_with_flag_on(monkeypatch):
    """The 'empty allowlist' decision: KLAI_PII_ENFORCE=true with
    KLAI_PII_ENFORCE_ORG_IDS unset/empty must behave exactly like the flag
    being off -- "no orgs", not "every org". See _org_is_enforced's
    docstring for the full argument."""
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": ""})
    assert mod.KLAI_PII_ENFORCE_ORG_IDS == frozenset()
    client = _ScriptedAnalyzerClient(raise_exc=AssertionError("must not call analyzer"))
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-allowlist-3",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    assert result is data
    assert client.calls == []


@pytest.mark.asyncio
async def test_missing_org_id_is_never_enforced_even_with_nonempty_allowlist(monkeypatch):
    """A request with no org_id (widget/partner master-key path) can never
    match an org allowlist -- defined, deliberate behaviour, not a gap."""
    mod = _load_enforcer(
        monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"}
    )
    client = _ScriptedAnalyzerClient(raise_exc=AssertionError("must not call analyzer"))
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-allowlist-4",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key(org_id=None), None, data, "completion"
    )
    assert result is data
    assert client.calls == []


def test_parse_org_allowlist_trims_whitespace_and_drops_empties():
    from klai_pii_enforce import _parse_org_allowlist

    assert _parse_org_allowlist("org1, org2 ,, org3") == frozenset({"org1", "org2", "org3"})
    assert _parse_org_allowlist("") == frozenset()
    assert _parse_org_allowlist("   ") == frozenset()


@pytest.mark.asyncio
async def test_wildcard_allowlist_enforces_an_org_it_never_names(monkeypatch):
    """General availability: `*` covers a tenant that appears after the
    variable was set, which is the case an enumerated list gets wrong --
    silently, and in the unsafe direction."""
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "*"})
    _stub_org_policy(mod, monkeypatch, frozenset())

    text = "mijn bsn is 111222333 graag verwerken"
    start, end = text.index("111222333"), text.index("111222333") + len("111222333")
    client = _ScriptedAnalyzerClient(
        script={text: [{"entity_type": "NL_BSN", "start": start, "end": end, "score": 0.85}]}
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-wildcard-1",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("an-org-nobody-listed"), None, data, "completion"
    )
    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert "<NL_BSN_1>" in user_message["content"]
    assert client.calls


@pytest.mark.asyncio
async def test_wildcard_still_never_enforces_a_request_without_org_id(monkeypatch):
    """`*` widens which identities match, not whether an identity is
    required. The org-less master-key path stays exempt, so decision (2)
    in `_org_is_enforced` survives general availability."""
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "*"})
    client = _ScriptedAnalyzerClient(raise_exc=AssertionError("must not call analyzer"))
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-wildcard-2",
        "messages": [{"role": "user", "content": "mijn bsn is 111222333"}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key(org_id=None), None, data, "completion"
    )
    assert result is data
    assert client.calls == []


@pytest.mark.asyncio
async def test_wildcard_masks_never_restore_entities_for_an_org_with_empty_policy(monkeypatch):
    """What "on for all tenants" actually delivers on day one: an org that
    has opted into nothing still gets SECRET and NL_BSN masked, and still
    gets no RETURN_SET entity masked. Guards against the wildcard being
    mistaken for "every entity on for everyone"."""
    mod = _load_enforcer(monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "*"})
    _stub_org_policy(mod, monkeypatch, frozenset())

    email = "jan@example.nl"
    text = f"mijn bsn is 111222333 en mijn mail is {email}"
    bsn_start = text.index("111222333")
    email_start = text.index(email)
    client = _ScriptedAnalyzerClient(
        script={
            text: [
                {
                    "entity_type": "NL_BSN",
                    "start": bsn_start,
                    "end": bsn_start + len("111222333"),
                    "score": 0.85,
                },
                {
                    "entity_type": "EMAIL_ADDRESS",
                    "start": email_start,
                    "end": email_start + len(email),
                    "score": 1.0,
                },
            ]
        }
    )
    _install_analyzer(mod, monkeypatch, client)

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-wildcard-3",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org-with-no-opt-ins"), None, data, "completion"
    )
    sent = [m for m in result["messages"] if m.get("role") == "user"][0]["content"]
    assert "<NL_BSN_1>" in sent, "never-restore entities apply the moment the org is enforced"
    assert email in sent, "a RETURN_SET entity stays unmasked until the org opts into it"


# ===========================================================================
# System-review finding M4 — length cap / chunking on the enforce path
# ===========================================================================
# REQ-10's fail-closed contract forbids analysing less than the whole
# outbound text while forwarding the rest unmasked. These tests exercise
# the chunking machinery directly (`_chunk_windows`, `_analyze_spans`,
# `_analyze_spans_chunked`) with small monkeypatched cap/overlap constants
# so they run fast and deterministically -- the real 20_000/6_000 values
# are exercised implicitly by every other test staying under that size and
# taking the single-call path.
@pytest.mark.asyncio
async def test_analyze_spans_makes_one_call_for_text_at_or_under_the_cap(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 20)

    text = "x" * 20
    client = _ScriptedAnalyzerClient(script={text: []})
    spans = await mod._analyze_spans(client, text, "en")

    assert spans == []
    assert len(client.calls) == 1
    assert client.calls[0]["json"]["text"] == text


@pytest.mark.asyncio
async def test_analyze_spans_dispatches_to_chunking_above_the_cap(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 10)
    monkeypatch.setattr(mod, "_CHUNK_OVERLAP_CHARS", 3)

    text = "a" * 25
    client = _ScriptedAnalyzerClient(default=[])
    spans = await mod._analyze_spans(client, text, "en")

    assert spans == []
    assert len(client.calls) > 1
    # Sol-review finding: an earlier version padded a full-size core on
    # both sides, so an interior call could reach _MAX_ANALYZE_CHARS +
    # 2*_CHUNK_OVERLAP_CHARS -- well past the cap the constant's own name
    # promises. Every individual call must now stay AT the cap itself.
    assert all(len(c["json"]["text"]) <= 10 for c in client.calls)


def test_chunk_windows_partition_the_full_text_with_no_gap_or_overlap_in_core(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 10)
    monkeypatch.setattr(mod, "_CHUNK_OVERLAP_CHARS", 3)

    windows = mod._chunk_windows(25)
    assert windows[0].core_start == 0
    assert windows[-1].core_end == 25
    for prev, nxt in zip(windows, windows[1:]):
        assert prev.core_end == nxt.core_start  # exact partition, no gap/overlap
    for w in windows:
        assert w.window_start == max(0, w.core_start - 3)
        assert w.window_end == min(25, w.core_end + 3)
        # padding guarantees any entity starting in the core is fully inside window
        assert w.window_end - w.core_start >= (w.core_end - w.core_start)
        # Sol-review finding: the padded window itself must never exceed
        # the cap -- that is what "_MAX_ANALYZE_CHARS bounds a single
        # /analyze call" actually has to mean.
        assert w.window_end - w.window_start <= 10


def test_chunk_windows_never_exceed_the_cap_at_production_constants(monkeypatch):
    """Same guarantee as the test above, pinned at the REAL production
    values (20_000 / 6_000) rather than small test constants -- this is
    the exact configuration that shipped, not just the algorithm in the
    abstract."""
    mod = _load_enforcer(monkeypatch, enforce=True)
    assert mod._MAX_ANALYZE_CHARS == 20_000
    assert mod._CHUNK_OVERLAP_CHARS == 6_000

    # A payload well above the cap, with several windows.
    windows = mod._chunk_windows(100_000)
    assert len(windows) > 1
    for w in windows:
        assert w.window_end - w.window_start <= mod._MAX_ANALYZE_CHARS
        assert w.core_end - w.core_start > 0
    # Cores still partition the full text exactly.
    assert windows[0].core_start == 0
    assert windows[-1].core_end == 100_000
    for prev, nxt in zip(windows, windows[1:]):
        assert prev.core_end == nxt.core_start


def test_chunk_windows_guards_against_overlap_consuming_the_whole_core(monkeypatch):
    """If overlap*2 >= cap (a misconfiguration), core_size would be <= 0,
    which without a floor means core_start never advances -- an infinite
    loop. Confirms the floor holds and the function still terminates."""
    mod = _load_enforcer(monkeypatch, enforce=True)
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 10)
    monkeypatch.setattr(mod, "_CHUNK_OVERLAP_CHARS", 10)  # overlap*2 > cap

    windows = mod._chunk_windows(30)  # must terminate, not hang
    assert windows[0].core_start == 0
    assert windows[-1].core_end == 30
    for prev, nxt in zip(windows, windows[1:]):
        assert prev.core_end == nxt.core_start


def test_chunk_windows_single_window_when_text_at_or_under_cap(monkeypatch):
    mod = _load_enforcer(monkeypatch, enforce=True)
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 100)
    monkeypatch.setattr(mod, "_CHUNK_OVERLAP_CHARS", 20)

    windows = mod._chunk_windows(100)
    assert len(windows) == 1
    assert windows[0] == mod._ChunkWindow(0, 100, 0, 100)


@pytest.mark.asyncio
async def test_chunked_analysis_detects_entity_straddling_a_window_boundary(monkeypatch):
    """The overlap padding must be wide enough that an entity whose span
    crosses a core boundary is still fully visible (and therefore
    detected) inside the window that owns it -- and counted exactly ONCE,
    not once per window that happens to see it."""
    mod = _load_enforcer(monkeypatch, enforce=True)
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 30)
    monkeypatch.setattr(mod, "_CHUNK_OVERLAP_CHARS", 10)

    phone = "0612345678"  # 10 chars
    text = ("x" * 15) + phone + ("y" * 15)  # phone straddles the core boundary at 20
    phone_start = text.index(phone)
    phone_end = phone_start + len(phone)
    windows = mod._chunk_windows(len(text))
    assert len(windows) > 1  # sanity: chunking actually engaged

    class _StraddleClient:
        def __init__(self):
            self.calls = []

        async def post(self, url, json=None, **kwargs):
            self.calls.append(json)
            window_text = json["text"]
            local_start = window_text.find(phone)
            if local_start == -1:
                return _FakeAnalyzerResponse([])
            return _FakeAnalyzerResponse(
                [
                    {
                        "entity_type": "PHONE_NUMBER",
                        "start": local_start,
                        "end": local_start + len(phone),
                        "score": 0.9,
                    }
                ]
            )

    client = _StraddleClient()
    spans = await mod._analyze_spans_chunked(client, text, "nl")

    assert len(spans) == 1  # not double-counted across overlapping windows
    assert spans[0].start == phone_start
    assert spans[0].end == phone_end
    assert spans[0].entity_type == "PHONE_NUMBER"


@pytest.mark.asyncio
async def test_chunked_analysis_covers_the_full_text_including_the_tail(monkeypatch):
    """REQ-10: no silent truncation on the enforce path. An entity that
    only appears near the very end of an oversized payload must still be
    found -- chunking, not refusal or truncation, is the answer here."""
    mod = _load_enforcer(monkeypatch, enforce=True)
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 50)
    monkeypatch.setattr(mod, "_CHUNK_OVERLAP_CHARS", 10)

    bsn = "111222333"
    text = ("filler tekst zonder iets gevoeligs " * 5) + f"bsn: {bsn}"
    assert len(text) > 50

    class _TailClient:
        def __init__(self):
            self.calls = 0

        async def post(self, url, json=None, **kwargs):
            self.calls += 1
            window_text = json["text"]
            local_start = window_text.find(bsn)
            if local_start == -1:
                return _FakeAnalyzerResponse([])
            return _FakeAnalyzerResponse(
                [
                    {
                        "entity_type": "NL_BSN",
                        "start": local_start,
                        "end": local_start + len(bsn),
                        "score": 1.0,
                    }
                ]
            )

    client = _TailClient()
    spans = await mod._analyze_spans_chunked(client, text, "nl")

    assert len(spans) == 1
    assert text[spans[0].start : spans[0].end] == bsn
    assert client.calls > 1  # confirms multiple windows were actually analysed


@pytest.mark.asyncio
async def test_oversized_payload_through_the_real_pre_call_hook_masks_the_whole_text(
    monkeypatch,
):
    """End-to-end smoke test through async_pre_call_hook (not just the
    chunking helpers directly): a payload far above the cap still gets its
    BSN masked, wherever it sits in the text."""
    mod = _load_enforcer(
        monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": "org1"}
    )
    monkeypatch.setattr(mod, "_MAX_ANALYZE_CHARS", 30)
    monkeypatch.setattr(mod, "_CHUNK_OVERLAP_CHARS", 10)
    _stub_org_policy(mod, monkeypatch, frozenset())

    bsn = "111222333"
    text = ("veel tekst zonder iets gevoeligs " * 4) + f"mijn bsn is {bsn} graag"

    class _E2EClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, url, json=None, **kwargs):
            window_text = json["text"]
            local_start = window_text.find(bsn)
            if local_start == -1:
                return _FakeAnalyzerResponse([])
            return _FakeAnalyzerResponse(
                [
                    {
                        "entity_type": "NL_BSN",
                        "start": local_start,
                        "end": local_start + len(bsn),
                        "score": 1.0,
                    }
                ]
            )

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: _E2EClient()))

    data = {
        "model": "klai-primary",
        "litellm_call_id": "call-oversized-1",
        "messages": [{"role": "user", "content": text}],
    }
    result = await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key("org1"), None, data, "completion"
    )
    user_message = [m for m in result["messages"] if m.get("role") == "user"][0]
    assert bsn not in user_message["content"]
    assert "<NL_BSN_1>" in user_message["content"]


# ===========================================================================
# 14. Telemetry — metadata only, and only for a tenant that allows it
# ===========================================================================
_TELEMETRY_IBAN = "NL91ABNA0417164300"


_TELEMETRY_ORG = "org-telemetry"


def _load_telemetry_enforcer(monkeypatch):
    return _load_enforcer(
        monkeypatch, enforce=True, extra_env={"KLAI_PII_ENFORCE_ORG_IDS": _TELEMETRY_ORG}
    )


async def _mask_one_iban(mod, monkeypatch, *, telemetry_level):
    """Run the real pre-call hook over a message with one IBAN in it."""
    text = f"Betaal op {_TELEMETRY_IBAN} graag."
    start = text.index(_TELEMETRY_IBAN)
    client = _ScriptedAnalyzerClient(
        script={
            text: [
                {
                    "entity_type": "IBAN_CODE",
                    "start": start,
                    "end": start + len(_TELEMETRY_IBAN),
                    "score": 1.0,
                }
            ]
        }
    )
    _install_analyzer(mod, monkeypatch, client)
    _stub_org_policy(
        mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level=telemetry_level
    )
    data = {"messages": [{"role": "user", "content": text}], "litellm_call_id": "call-telemetry"}
    return await mod.klai_pii_enforcer.async_pre_call_hook(
        _user_api_key(_TELEMETRY_ORG), None, data, "acompletion"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["shadow", "full"])
async def test_mask_telemetry_reports_counts_for_a_tenant_that_allows_it(
    monkeypatch, caplog, level
):
    mod = _load_telemetry_enforcer(monkeypatch)
    with caplog.at_level(logging.INFO):
        await _mask_one_iban(mod, monkeypatch, telemetry_level=level)

    masked_lines = [r.getMessage() for r in caplog.records if "pii_masked" in r.getMessage()]
    assert len(masked_lines) == 1
    assert "'IBAN_CODE': 1" in masked_lines[0]
    assert "restorable=1" in masked_lines[0]


@pytest.mark.asyncio
async def test_mask_telemetry_is_silent_for_a_tenant_on_off(monkeypatch, caplog):
    """``off`` means zero telemetry — a counter about a tenant is still telemetry."""
    mod = _load_telemetry_enforcer(monkeypatch)
    with caplog.at_level(logging.INFO):
        data = await _mask_one_iban(mod, monkeypatch, telemetry_level="off")

    assert not [r for r in caplog.records if "pii_masked" in r.getMessage()]
    # Masking itself still happened — telemetry is gated, enforcement is not.
    assert _TELEMETRY_IBAN not in str(data["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["off", "shadow", "full"])
async def test_no_telemetry_line_ever_contains_the_masked_value(monkeypatch, caplog, level):
    """The whole point. A log line that leaks the IBAN defeats the feature."""
    mod = _load_telemetry_enforcer(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        await _mask_one_iban(mod, monkeypatch, telemetry_level=level)

    for record in caplog.records:
        assert _TELEMETRY_IBAN not in record.getMessage()


@pytest.mark.asyncio
async def test_restore_telemetry_counts_a_surviving_placeholder(monkeypatch, caplog):
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-restore-survived"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    with caplog.at_level(logging.INFO):
        await mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": call_id},
            _user_api_key(_TELEMETRY_ORG),
            _response("Het rekeningnummer is <IBAN_CODE_1> volgens de mail."),
        )

    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert len(lines) == 1
    assert "expected=1" in lines[0]
    assert "survived=1" in lines[0]
    assert "leaked=0" in lines[0]


@pytest.mark.asyncio
async def test_restore_telemetry_counts_a_placeholder_the_model_mangled(monkeypatch, caplog):
    """REQ-0b's failure mode: the model did not hand the token back verbatim.

    The user sees damage and nothing else notices. This counter is the only
    production signal that it happened.
    """
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-restore-mangled"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    with caplog.at_level(logging.INFO):
        await mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": call_id},
            _user_api_key(_TELEMETRY_ORG),
            _response("Het rekeningnummer is <iban_code_1> volgens de mail."),
        )

    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert len(lines) == 1
    assert "survived=0" in lines[0]
    assert "leaked=1" in lines[0]


@pytest.mark.asyncio
async def test_restore_telemetry_counts_once_across_stream_chunks(monkeypatch, caplog):
    """A placeholder split across chunks must not be counted twice.

    ``split_safe_tail`` returns the already-restored tail, which the next
    iteration prepends — so a naive counter that re-scanned the buffer every
    round would double-count. This pins that it does not.
    """
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-restore-stream"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    with caplog.at_level(logging.INFO):
        chunks = await _drain(
            mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
                _user_api_key(_TELEMETRY_ORG),
                _achunks("Het nummer is <IBAN_C", "ODE_1> volgens de mail."),
                {"litellm_call_id": call_id},
            )
        )

    assert _TELEMETRY_IBAN in _all_content(chunks)
    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert len(lines) == 1
    assert "expected=1" in lines[0]
    assert "survived=1" in lines[0]
    assert "streamed=True" in lines[0]
    assert "completed=True" in lines[0]


@pytest.mark.asyncio
async def test_restore_telemetry_is_silent_for_a_tenant_on_off(monkeypatch, caplog):
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="off")
    call_id = "call-restore-off"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    with caplog.at_level(logging.INFO):
        response = await mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": call_id},
            _user_api_key(_TELEMETRY_ORG),
            _response("Het rekeningnummer is <IBAN_CODE_1>."),
        )

    assert not [r for r in caplog.records if "pii_restored" in r.getMessage()]
    # Restore itself is not gated on telemetry.
    assert _TELEMETRY_IBAN in response.choices[0].message.content


@pytest.mark.asyncio
async def test_restore_telemetry_marks_an_interrupted_stream_as_incomplete(monkeypatch, caplog):
    """A stream that dies halfway is exactly when tokens go out unrestored.

    Emitting nothing there would blind the counter to its own worst case, so
    the line is emitted with ``completed=False`` rather than skipped.
    """
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-restore-interrupted"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    async def _dying_stream():
        yield _chunk("Het nummer is ")
        raise RuntimeError("upstream died")

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError):
            await _drain(
                mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
                    _user_api_key(_TELEMETRY_ORG), _dying_stream(), {"litellm_call_id": call_id}
                )
            )

    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert len(lines) == 1
    assert "completed=False" in lines[0]


@pytest.mark.asyncio
async def test_restore_telemetry_failure_does_not_break_the_response(monkeypatch, caplog):
    """A telemetry bug must not turn a served answer into an error."""
    mod = _load_enforcer(monkeypatch, enforce=True)

    async def _boom(org_id):
        raise RuntimeError("portal-api on fire")

    monkeypatch.setattr(mod, "resolve_org_pii_context", _boom)
    call_id = "call-restore-boom"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    response = await mod.klai_pii_enforcer.async_post_call_success_hook(
        {"litellm_call_id": call_id},
        _user_api_key(_TELEMETRY_ORG),
        _response("Het rekeningnummer is <IBAN_CODE_1>."),
    )

    assert _TELEMETRY_IBAN in response.choices[0].message.content


@pytest.mark.asyncio
async def test_restore_telemetry_survives_a_client_disconnect(monkeypatch, caplog):
    """Closing the stream early must not raise, and must still report.

    A ``finally`` in an async generator runs under ``GeneratorExit`` when the
    consumer goes away. Awaiting there raises "async generator ignored
    GeneratorExit" — a client hangup turned into a server error. The telemetry
    level is therefore resolved before the first yield and the emitter is
    synchronous; this test is what fails if either changes back.
    """
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-restore-disconnect"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    agen = mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
        _user_api_key(_TELEMETRY_ORG),
        _achunks("Het nummer is ", "<IBAN_CODE_1>", " en dat was het."),
        {"litellm_call_id": call_id},
    )
    with caplog.at_level(logging.INFO):
        await agen.__anext__()
        await agen.aclose()

    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert len(lines) == 1
    assert "completed=False" in lines[0]
    # REQ-11 still holds on the abandoned-stream path.
    assert mod._pii_map_store.get(call_id) is None


@pytest.mark.asyncio
async def test_cancellation_before_the_first_chunk_still_discards_the_map(monkeypatch):
    """REQ-11: the placeholder→value map is the only place the raw PII lives.

    The telemetry-level resolve is an await that happens before the first
    chunk. If it sat outside the `try`, a task cancelled during it would end
    the generator without running the `finally` — leaving the map in process
    memory until the TTL sweep. It sits inside the `try` for exactly that
    reason.
    """
    mod = _load_enforcer(monkeypatch, enforce=True)
    call_id = "call-cancelled-early"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    async def _never_resolves(_user_api_key_dict):
        await asyncio.sleep(3600)

    monkeypatch.setattr(
        mod.KlaiPiiEnforcer, "_resolve_telemetry_level", staticmethod(_never_resolves)
    )

    agen = mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
        _user_api_key(_TELEMETRY_ORG), _achunks("hallo"), {"litellm_call_id": call_id}
    )
    task = asyncio.create_task(agen.__anext__())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await agen.aclose()

    assert mod._pii_map_store.get(call_id) is None


@pytest.mark.asyncio
async def test_a_consumer_that_stops_after_the_last_chunk_reports_completed(
    monkeypatch, caplog
):
    """`completed` marks a partial tally, not a partial delivery.

    Once the flush has run the count is final; a consumer that takes the last
    item and walks away has not truncated anything, so reporting
    completed=False there would be noise in the one field that exists to mark
    a genuinely partial count.
    """
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-consumer-stops"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    agen = mod.klai_pii_enforcer.async_post_call_streaming_iterator_hook(
        _user_api_key(_TELEMETRY_ORG),
        _achunks("Het nummer is <IBAN_CODE_1>", " en dat was het."),
        {"litellm_call_id": call_id},
    )
    with caplog.at_level(logging.INFO):
        received = []
        try:
            while True:
                received.append(await agen.__anext__())
                if len(received) == 2:  # everything the hook will ever emit
                    break
        except StopAsyncIteration:  # pragma: no cover - defensive
            pass
        await agen.aclose()

    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert len(lines) == 1
    assert "completed=True" in lines[0]
    assert "survived=1" in lines[0]


@pytest.mark.asyncio
async def test_a_value_the_model_never_mentions_is_not_reported_as_damage(monkeypatch, caplog):
    """The metric this replaced would have called this a restore failure.

    Three values masked, the model answers about none of them. Nothing reached
    the user damaged, so `leaked` is 0 — even though `expected - survived` is
    3. That subtraction is the shape this started out with; in ordinary chat it
    is dominated by values whose absence harms nobody, which would have buried
    the real failures under routine answers.
    """
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-never-mentioned"
    mod._pii_map_store.put(
        call_id,
        {
            "<IBAN_CODE_1>": _TELEMETRY_IBAN,
            "<EMAIL_ADDRESS_1>": "jan@example.nl",
            "<PHONE_NUMBER_1>": "0612345678",
        },
    )

    with caplog.at_level(logging.INFO):
        await mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": call_id},
            _user_api_key(_TELEMETRY_ORG),
            _response("Ja, dat klopt."),
        )

    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert len(lines) == 1
    assert "expected=3" in lines[0]
    assert "survived=0" in lines[0]
    assert "leaked=0" in lines[0]


@pytest.mark.asyncio
async def test_a_mangled_placeholder_the_user_can_see_counts_as_leaked(monkeypatch, caplog):
    """The case the counter exists for: the reader gets angle brackets."""
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-leaked"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    with caplog.at_level(logging.INFO):
        restored = await mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": call_id},
            _user_api_key(_TELEMETRY_ORG),
            _response("Het rekeningnummer is <iban_code_1>."),
        )

    assert "<iban_code_1>" in restored.choices[0].message.content  # really is visible
    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert "survived=0" in lines[0]
    assert "leaked=1" in lines[0]


@pytest.mark.asyncio
async def test_a_never_restore_placeholder_is_not_counted_as_leaked(monkeypatch, caplog):
    """`<NL_BSN_1>` staying put is REQ-8 working, not damage."""
    mod = _load_enforcer(monkeypatch, enforce=True)
    _stub_org_policy(mod, monkeypatch, frozenset({"IBAN_CODE"}), telemetry_level="shadow")
    call_id = "call-never-restore"
    mod._pii_map_store.put(call_id, {"<IBAN_CODE_1>": _TELEMETRY_IBAN})

    with caplog.at_level(logging.INFO):
        await mod.klai_pii_enforcer.async_post_call_success_hook(
            {"litellm_call_id": call_id},
            _user_api_key(_TELEMETRY_ORG),
            _response("Het BSN <NL_BSN_1> hoort bij <IBAN_CODE_1>."),
        )

    lines = [r.getMessage() for r in caplog.records if "pii_restored" in r.getMessage()]
    assert "survived=1" in lines[0]
    assert "leaked=0" in lines[0]

