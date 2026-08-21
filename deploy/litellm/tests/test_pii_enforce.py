"""Tests for klai_pii_enforce.py (SPEC-PRIVACY-MISTRAL-PII-001 Phase 3,
REQ-7 through REQ-11).

litellm is not installed as the real proxy package for these tests (see
test_klai_knowledge_hook.py / test_pii_observe.py's same rationale) — the
import is mocked so KlaiPiiEnforcer's CustomLogger base class resolves.
"""

from __future__ import annotations

import asyncio
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


def _stub_org_policy(mod, monkeypatch, policy: frozenset[str] | dict[str, frozenset[str]]):
    """Replace resolve_org_entity_policy with a deterministic stub.

    ``policy`` may be a single frozenset (same answer for every org) or a
    dict keyed by org_id for multi-org (cross-tenant) tests.
    """

    async def _resolve(org_id):
        if isinstance(policy, dict):
            return policy.get(org_id, frozenset())
        return policy

    monkeypatch.setattr(mod, "resolve_org_entity_policy", _resolve)


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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
    mod = _load_enforcer(monkeypatch, enforce=True)
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
