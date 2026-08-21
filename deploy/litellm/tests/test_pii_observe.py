"""Tests for klai_pii_observe.py (SPEC-PRIVACY-MISTRAL-PII-001 Phase 2).

litellm is not installed as the real proxy package for these tests (see
test_klai_knowledge_hook.py's same rationale) — we mock the import so
KlaiPiiObserver's CustomLogger base class resolves.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


@pytest.fixture(autouse=True)
def _mock_litellm():
    """Mock litellm module so klai_pii_observe can be imported."""
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


def _load_observer(monkeypatch, extra_env=None):
    env = {"PRESIDIO_ANALYZER_API_BASE": "http://presidio-analyzer:3000"}
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_klai_kb_modules()
    import klai_pii_observe

    return klai_pii_observe


def _user_api_key(org_id=None):
    uak = MagicMock()
    uak.metadata = {"org_id": org_id} if org_id is not None else {}
    return uak


class _FakeAnalyzerResponse:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return self._results


class _FakeAsyncClient:
    """Records every POST and returns a scripted result list."""

    def __init__(self, results=None, hang_event=None):
        self._results = results if results is not None else []
        self.calls = []
        self._hang_event = hang_event

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    @property
    def last_text(self):
        """Text of the most recent /analyze POST — what the analyzer saw."""
        return self.calls[-1]["json"]["text"] if self.calls else ""

    async def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json})
        if self._hang_event is not None:
            await self._hang_event.wait()
        return _FakeAnalyzerResponse(self._results)


async def _drain_background_tasks():
    """Let any create_task()-scheduled coroutines finish before a test ends."""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current]
    if pending:
        await asyncio.wait(pending, timeout=2.0)


# ---------------------------------------------------------------------------
# Payload is returned unchanged
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_payload_returned_byte_identical(monkeypatch):
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-primary",
        "messages": [{"role": "user", "content": "hello there, how are you today?"}],
    }
    original_identity = data

    result = await mod.klai_pii_observer.async_pre_call_hook(
        _user_api_key("org123"), None, data, "completion"
    )

    # Not just equal — the exact same object. REQ-5: "return the payload
    # completely unchanged."
    assert result is original_identity
    assert result == {
        "model": "klai-primary",
        "messages": [{"role": "user", "content": "hello there, how are you today?"}],
    }
    await _drain_background_tasks()


# ---------------------------------------------------------------------------
# AC-7 / AC-8 — the two KlaiKnowledgeHook blind spots do NOT suppress this
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openai_passthrough_flag_does_not_suppress_observation(monkeypatch):
    """AC-7: a request carrying `_klai_openai_passthrough` and a BSN is
    still evaluated and counted — the regression test for the hook's
    blind spot named in REQ-5.
    """
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[{"entity_type": "NL_BSN"}])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-primary",
        "metadata": {"_klai_openai_passthrough": True},
        "messages": [
            {"role": "user", "content": "mijn bsn is 111222333, kun je dit verwerken alsjeblieft?"}
        ],
    }

    await mod.klai_pii_observer.async_pre_call_hook(
        _user_api_key("org123"), None, data, "completion"
    )
    await _drain_background_tasks()

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_missing_org_id_is_still_observed(monkeypatch, caplog):
    """AC-8: a request with no org_id (widget/partner shape, master-key
    calls) is still evaluated and counted.
    """
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[{"entity_type": "EMAIL_ADDRESS"}])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-fast",
        "messages": [{"role": "user", "content": "contact me at jan@example.com please, thanks"}],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key(org_id=None), None, data, "completion"
        )
        await _drain_background_tasks()

    assert len(client.calls) == 1
    assert any("pii_observed" in r.message for r in caplog.records)
    assert any("org_id=None" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# AC-9 — no matched value, offset, or hash ever leaves the module
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_emitted_event_contains_no_value_offset_or_hash(monkeypatch, caplog):
    mod = _load_observer(monkeypatch)
    bsn_digits = "111222333"
    # Analyzer response shaped the way real Presidio /analyze responds:
    # entity_type + start/end/score. The observer must discard everything
    # except entity_type.
    client = _FakeAsyncClient(
        results=[{"entity_type": "NL_BSN", "start": 12, "end": 21, "score": 0.85}]
    )
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-primary",
        "messages": [{"role": "user", "content": f"mijn burgerservicenummer is {bsn_digits} graag verwerken"}],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    all_logged_text = "\n".join(r.message for r in caplog.records)
    assert bsn_digits not in all_logged_text
    assert "start" not in all_logged_text
    assert "end=" not in all_logged_text
    assert "0.85" not in all_logged_text
    # A hash of a BSN is a BSN (REQ-6) — no hex digest of any length either.
    import hashlib
    import re

    assert hashlib.sha256(bsn_digits.encode()).hexdigest() not in all_logged_text
    assert not re.search(r"\b[0-9a-f]{32,64}\b", all_logged_text)
    # Only the count survives.
    assert "NL_BSN" in all_logged_text
    assert "entity_counts={'NL_BSN': 1}" in all_logged_text


# ---------------------------------------------------------------------------
# Entity in a non-last message is counted
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_entity_in_non_last_message_is_counted(monkeypatch, caplog):
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[{"entity_type": "NL_BSN"}])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-primary",
        "messages": [
            {"role": "user", "content": "mijn bsn is 111222333, kun je dit onthouden?"},
            {"role": "assistant", "content": "Genoteerd, dank u wel."},
            {"role": "user", "content": "kun je nu een samenvatting maken?"},
        ],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    # The combined text sent to the analyzer must include the FIRST
    # message's content, not just the last user turn.
    assert len(client.calls) == 1
    assert "111222333" in client.calls[0]["json"]["text"]
    assert any("entity_counts={'NL_BSN': 1}" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Analyzer failure/timeout is swallowed — request unaffected
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_analyzer_error_is_swallowed_and_logged(monkeypatch, caplog):
    mod = _load_observer(monkeypatch)

    class _RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, *args, **kwargs):
            raise ConnectionError("presidio-analyzer unreachable")

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: _RaisingClient()))

    data = {
        "model": "klai-primary",
        "messages": [{"role": "user", "content": "just a normal chat message, nothing special here"}],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        result = await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    assert result is data
    assert any("pii_observe_failed" in r.message for r in caplog.records)
    assert not any("pii_observed " in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_analyzer_500_is_swallowed_and_logged(monkeypatch, caplog):
    mod = _load_observer(monkeypatch)

    class _HttpErrorResponse:
        def raise_for_status(self):
            raise RuntimeError("500 Internal Server Error")

        def json(self):
            return []

    class _ErrorClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def post(self, *args, **kwargs):
            return _HttpErrorResponse()

    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: _ErrorClient()))

    data = {
        "model": "klai-primary",
        "messages": [{"role": "user", "content": "just a normal chat message, nothing special here"}],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        result = await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    assert result is data
    assert any("pii_observe_failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Out-of-band: the hook returns without waiting for a slow analyzer
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hook_does_not_await_the_analyzer_call(monkeypatch):
    """Concrete proof of REQ-5's "out of band" requirement: even when the
    analyzer never responds, the hook itself returns almost immediately.
    If the hook awaited the analyzer call directly, this would hang until
    the outer `asyncio.wait_for` below raised `TimeoutError`.
    """
    mod = _load_observer(monkeypatch)
    hang_event = asyncio.Event()  # never set during the timed section
    client = _FakeAsyncClient(results=[], hang_event=hang_event)
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-primary",
        "messages": [{"role": "user", "content": "hello there, how are you doing today?"}],
    }

    started = time.monotonic()
    result = await asyncio.wait_for(
        mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        ),
        timeout=1.0,
    )
    elapsed = time.monotonic() - started

    assert result is data
    # The analyzer call is gated on an Event that is never set in this
    # section — a blocking implementation would need the full 1.0s
    # `wait_for` timeout (or hang forever without it). An out-of-band
    # implementation returns near-instantly regardless.
    assert elapsed < 0.1, f"hook took {elapsed:.3f}s — looks like it awaited the analyzer call"

    # Let the background task make progress and finish so nothing leaks
    # past the test.
    hang_event.set()
    await _drain_background_tasks()


@pytest.mark.asyncio
async def test_no_running_loop_does_not_raise(monkeypatch):
    """When called outside a running loop (defensive path — LiteLLM always
    calls hooks from inside its own event loop, but the observer must not
    assume that), scheduling failure is swallowed too.

    Patches ``get_running_loop`` itself to raise (the real no-loop
    behaviour), rather than returning a loop whose ``create_task`` raises —
    the latter would still construct the ``_observe(...)`` coroutine as
    the call argument before the raise, leaking an unawaited coroutine.
    """
    mod = _load_observer(monkeypatch)

    def _raise_no_running_loop():
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(mod.asyncio, "get_running_loop", _raise_no_running_loop)

    data = {
        "model": "klai-primary",
        "messages": [{"role": "user", "content": "hello there, how are you doing today?"}],
    }

    result = await mod.klai_pii_observer.async_pre_call_hook(
        _user_api_key("org123"), None, data, "completion"
    )
    assert result is data


# ---------------------------------------------------------------------------
# Detected language rides along in the event
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_event_carries_detected_dutch_language(monkeypatch, caplog):
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-primary",
        "messages": [
            {
                "role": "user",
                "content": "Kunt u dit voor mij regelen? Ik heb het niet zelf gedaan en wil het graag weten.",
            }
        ],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    assert any("language=nl" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_event_carries_detected_english_language(monkeypatch, caplog):
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-primary",
        "messages": [
            {
                "role": "user",
                "content": "Could you please help me with this and let me know what you think of it?",
            }
        ],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    assert any("language=en" in r.message for r in caplog.records)


def test_short_text_detects_as_unknown_language(monkeypatch):
    mod = _load_observer(monkeypatch)
    assert mod._detect_language("hoi") == mod.UNKNOWN_LANGUAGE
    assert mod._detect_language("") == mod.UNKNOWN_LANGUAGE


def test_analyzer_language_falls_back_to_supported_set(monkeypatch):
    mod = _load_observer(monkeypatch)
    assert "fr" not in mod._ANALYZER_SUPPORTED_LANGUAGES
    assert "nl" in mod._ANALYZER_SUPPORTED_LANGUAGES


# ---------------------------------------------------------------------------
# No messages -> nothing to observe, no analyzer call
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_messages_skips_analyzer_call(monkeypatch):
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {"model": "klai-bge-m3", "input": ["some embedding text"]}

    result = await mod.klai_pii_observer.async_pre_call_hook(
        _user_api_key("org123"), None, data, "embeddings"
    )
    await _drain_background_tasks()

    assert result is data
    assert client.calls == []


# ---------------------------------------------------------------------------
# Sol delta-review: tool_call arguments are provider-visible text
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tool_call_arguments_are_scanned(monkeypatch, caplog):
    """An assistant turn can carry content=None while tool_calls hold PII.

    The router keeps those turns and they reach Mistral verbatim, so a
    measurement reading only `content` under-reports exactly the agentic
    paths (klai-large, MCP) where PII moves around most.
    """
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[{"entity_type": "EMAIL_ADDRESS"}])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    data = {
        "model": "klai-large",
        "messages": [
            {"role": "user", "content": "stuur dit door"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "function": {
                            "name": "send_mail",
                            "arguments": '{"to": "jan.devries@example.nl"}',
                        }
                    }
                ],
            },
        ],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    # The arguments string must have reached the analyzer payload.
    assert "jan.devries@example.nl" in client.last_text
    assert "entity_counts={'EMAIL_ADDRESS': 1}" in "\n".join(
        r.message for r in caplog.records
    )
    # And the address itself must still not appear in the emitted event.
    assert "jan.devries@example.nl" not in "\n".join(r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Sol delta-review: language comes from the user turn, not the KB context
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_language_detected_from_user_turn_not_kb_context(monkeypatch, caplog):
    """The KB context block is deliberately English-structured.

    Detecting on the whole payload would label a Dutch question "en" on
    essentially every RAG request, making REQ-2's per-language recall
    comparison meaningless. The PII scan still covers the full payload.
    """
    mod = _load_observer(monkeypatch)
    client = _FakeAsyncClient(results=[])
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=lambda **kw: client))

    english_kb_block = (
        "[Klai Knowledge Base - use this as supplementary context for your answer. "
        "You may complement it with your general knowledge.] "
        "The following documents were retrieved from the knowledge base and are "
        "provided so that the assistant can ground its answer in them."
    )
    data = {
        "model": "klai-primary",
        "messages": [
            {"role": "system", "content": english_kb_block},
            {"role": "user", "content": "Kun je mij vertellen hoe ik dit moet doen met de nieuwe instellingen?"},
        ],
    }

    with caplog.at_level("WARNING", logger="klai_pii_observe"):
        await mod.klai_pii_observer.async_pre_call_hook(
            _user_api_key("org123"), None, data, "completion"
        )
        await _drain_background_tasks()

    logged = "\n".join(r.message for r in caplog.records)
    assert "language=nl" in logged, logged
    # The scan itself still saw the English block.
    assert "knowledge base" in client.last_text.lower()
