"""SPEC-RAG-QUERY-REWRITE-001 — _rewrite_query helper unit tests.

litellm is not installed locally (runs in Docker), so we mock the import via
the shared fixture in test_klai_knowledge_hook.py.
"""

import importlib
import sys
import types

import httpx
import pytest


@pytest.fixture(autouse=True)
def _mock_litellm():
    """Mock litellm module so klai_knowledge can be imported."""
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    integrations_mod.custom_logger = custom_logger_mod
    litellm_mod.integrations = integrations_mod

    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod

    yield


def _load_hook(monkeypatch, extra_env=None):
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_API_URL": "http://portal-api:8000",
        "MISTRAL_API_KEY": "test-mistral-key",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    sys.modules.pop("klai_knowledge", None)
    import klai_knowledge

    importlib.reload(klai_knowledge)
    return klai_knowledge


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int, json_body: dict | None = None) -> None:
        self._status_code = status_code
        self._json_body = json_body or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self._json_body).encode(),
            request=request,
        )


def _ok_response(content: str) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 25},
    }


_HISTORY_3_TURNS = [
    {"role": "user", "content": "Hoe gaat het met de portering van klant Jansen B.V.?"},
    {
        "role": "assistant",
        "content": "De uitportering van Jansen B.V. wacht op bevestiging van KPN.",
    },
    {
        "role": "user",
        "content": "Wat is de status van de aanvraag?",
    },
]


# ---------------------------------------------------------------------------
# Skip-conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_skips_when_no_history(monkeypatch):
    hook = _load_hook(monkeypatch)
    rewritten, meta = await hook._rewrite_query("Wat zei hij?", [])
    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "no_history"
    assert meta["was_changed"] is False


@pytest.mark.asyncio
async def test_rewrite_query_skips_when_disabled(monkeypatch):
    hook = _load_hook(monkeypatch, extra_env={"QUERY_REWRITE_ENABLED": "false"})
    rewritten, meta = await hook._rewrite_query("Wat zei hij?", _HISTORY_3_TURNS)
    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "disabled"


@pytest.mark.asyncio
async def test_rewrite_query_skips_when_no_api_key(monkeypatch):
    hook = _load_hook(monkeypatch, extra_env={"MISTRAL_API_KEY": ""})
    rewritten, meta = await hook._rewrite_query("Wat zei hij?", _HISTORY_3_TURNS)
    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "no_api_key"


@pytest.mark.asyncio
async def test_rewrite_query_skips_on_empty_query(monkeypatch):
    hook = _load_hook(monkeypatch)
    rewritten, meta = await hook._rewrite_query("", _HISTORY_3_TURNS)
    assert rewritten == ""
    assert meta["skipped"] == "empty_query"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_returns_rewritten_string_on_200(monkeypatch):
    hook = _load_hook(monkeypatch)
    rewritten_content = "Wat is de status van de portering-aanvraag van Jansen B.V.?"
    transport = _MockTransport(
        status_code=200, json_body=_ok_response(rewritten_content)
    )

    rewritten, meta = await hook._rewrite_query(
        "Wat is de status van de aanvraag?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert rewritten == rewritten_content
    assert meta["was_changed"] is True
    assert meta["rewrite_ms"] >= 0
    assert "skipped" not in meta


@pytest.mark.asyncio
async def test_rewrite_query_strips_surrounding_quotes(monkeypatch):
    """Mistral occasionally wraps the rewrite in quotes — strip them off."""
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(
        status_code=200,
        json_body=_ok_response('"Wat is de status van de portering van Jansen B.V.?"'),
    )

    rewritten, meta = await hook._rewrite_query(
        "Wat is de status van de aanvraag?",
        _HISTORY_3_TURNS,
        _transport=transport,
    )

    assert not rewritten.startswith('"')
    assert not rewritten.endswith('"')
    assert "Jansen" in rewritten


@pytest.mark.asyncio
async def test_rewrite_query_was_changed_false_when_identical(monkeypatch):
    """If the model returns the input unchanged, was_changed is False."""
    hook = _load_hook(monkeypatch)
    raw = "Hoe troubleshoot ik Bubble?"
    transport = _MockTransport(status_code=200, json_body=_ok_response(raw))

    rewritten, meta = await hook._rewrite_query(
        raw, _HISTORY_3_TURNS, _transport=transport
    )

    assert rewritten == raw
    assert meta["was_changed"] is False


# ---------------------------------------------------------------------------
# Failure modes — all fall back to raw_query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_query_falls_back_on_500(monkeypatch):
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(status_code=500, json_body={"detail": "boom"})

    rewritten, meta = await hook._rewrite_query(
        "Wat zei hij?", _HISTORY_3_TURNS, _transport=transport
    )

    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "exception"
    assert "error" in meta


@pytest.mark.asyncio
async def test_rewrite_query_falls_back_on_empty_response(monkeypatch):
    hook = _load_hook(monkeypatch)
    transport = _MockTransport(status_code=200, json_body=_ok_response(""))

    rewritten, meta = await hook._rewrite_query(
        "Wat zei hij?", _HISTORY_3_TURNS, _transport=transport
    )

    assert rewritten == "Wat zei hij?"
    assert meta["skipped"] == "empty_response"


# ---------------------------------------------------------------------------
# History formatting
# ---------------------------------------------------------------------------


def test_format_history_truncates_to_max_chars(monkeypatch):
    hook = _load_hook(monkeypatch)
    long_history = [
        {"role": "user", "content": "x" * 600},
        {"role": "assistant", "content": "y" * 600},
    ]
    formatted = hook._format_history_for_rewrite(long_history, max_chars=300)
    assert len(formatted) <= 320  # 300 + ellipsis + role prefix slack
    assert "…" in formatted


def test_format_history_skips_blank_content(monkeypatch):
    hook = _load_hook(monkeypatch)
    history = [
        {"role": "user", "content": "Real question"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "  "},
        {"role": "assistant", "content": "Real answer"},
    ]
    formatted = hook._format_history_for_rewrite(history)
    assert "Real question" in formatted
    assert "Real answer" in formatted
    # Two blanks dropped:
    assert formatted.count("USER:") == 1
    assert formatted.count("ASSISTANT:") == 1
