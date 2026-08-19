"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 5 — telemetry-level forwarding tests.

The LiteLLM hook MUST:
- Read ``telemetry_level`` from the kb_feature cache / portal response
- Default to ``shadow`` when the field is absent (REQ-4 fail-open)
- Pass ``telemetry_level`` in every ``/retrieve`` body (REQ-4)
- Gate the ``query_rewrite`` log line per REQ-6: in 'off' / 'shadow' the
  raw_query / rewritten_query kwargs MUST NOT appear in the log line

We re-use the existing test_klai_knowledge_hook test infrastructure —
``_load_hook``, ``_make_cache``, ``_make_user_api_key``, ``_make_resp``,
``_mock_litellm`` — so the privacy tests run under the exact same harness
as the rest of the hook tests.
"""

# noqa: I001
import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


def _load_test_helpers():
    """Load the existing test_klai_knowledge_hook module so we can reuse its
    fixtures + helpers without copying them. The autouse `_mock_litellm`
    fixture from that module is what makes ``import klai_knowledge`` work
    inside the docker-only LiteLLM environment.
    """
    helpers_path = Path(__file__).parent / "test_klai_knowledge_hook.py"
    spec = importlib.util.spec_from_file_location(
        "_klai_hook_test_helpers", helpers_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_klai_hook_test_helpers"] = module
    spec.loader.exec_module(module)
    return module


_helpers = _load_test_helpers()
_load_hook = _helpers._load_hook
_make_cache = _helpers._make_cache
_make_user_api_key = _helpers._make_user_api_key
_make_resp = _helpers._make_resp


@pytest.fixture(autouse=True)
def _mock_litellm():
    """Identical to test_klai_knowledge_hook's autouse fixture.

    Inlined here because pytest does not auto-apply autouse fixtures from
    a sibling test module; importing the helpers does not propagate the
    fixture registration.
    """
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass

        async def async_post_call_success_hook(self, *args, **kwargs):
            pass

        async def async_post_call_failure_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    litellm_mod.integrations = integrations_mod
    integrations_mod.custom_logger = custom_logger_mod

    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod

    yield

    for mod_name in [
        "litellm",
        "litellm.integrations",
        "litellm.integrations.custom_logger",
    ]:
        sys.modules.pop(mod_name, None)
    reset_klai_kb_modules()


def _data_payload() -> dict:
    return {
        "user": "aabbcc112233445566778899",
        "messages": [
            {"role": "user", "content": "Hoe stel ik vakantie aan?"},
        ],
    }


@pytest.mark.asyncio
async def test_retrieve_body_includes_telemetry_level_from_cache(monkeypatch):
    """Cache-hit path: the level from the cached feature dict reaches the
    /retrieve POST body verbatim."""
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()
    cache = _make_cache(feature_enabled=True, feature={"telemetry_level": "full"})

    mock_resp = _make_resp({"chunks": [], "retrieval_bypassed": False})
    with patch("klai_knowledge.httpx.AsyncClient") as cls:
        mc = AsyncMock()
        mc.post = AsyncMock(return_value=mock_resp)
        mc.get = AsyncMock(return_value=_make_resp({"enabled": True}))
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = mc

        await hook.async_pre_call_hook(
            _make_user_api_key(), cache, _data_payload(), "completion"
        )

        post_call = mc.post.call_args
        assert post_call is not None, "expected /retrieve POST to fire"
        body = post_call.kwargs.get("json") or {}
        assert body.get("telemetry_level") == "full"


@pytest.mark.asyncio
async def test_retrieve_body_defaults_to_shadow_when_field_absent(monkeypatch):
    """REQ-4 fail-open: a cache hit from an older portal-api build that
    pre-dates this SPEC has no telemetry_level field. The hook MUST
    default to 'shadow', never 'off'."""
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()
    cache = _make_cache(feature_enabled=True)  # no telemetry_level in feature

    mock_resp = _make_resp({"chunks": [], "retrieval_bypassed": False})
    with patch("klai_knowledge.httpx.AsyncClient") as cls:
        mc = AsyncMock()
        mc.post = AsyncMock(return_value=mock_resp)
        mc.get = AsyncMock(return_value=_make_resp({"enabled": True}))
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = mc

        await hook.async_pre_call_hook(
            _make_user_api_key(), cache, _data_payload(), "completion"
        )

        post_call = mc.post.call_args
        assert post_call is not None
        body = post_call.kwargs.get("json") or {}
        # REQ-4: default is 'shadow', never 'off'.
        assert body.get("telemetry_level") == "shadow"


@pytest.mark.asyncio
async def test_query_rewrite_log_redacts_in_shadow_mode(monkeypatch, caplog):
    """REQ-6: the literal raw_query / rewritten_query MUST NOT appear in
    the query_rewrite log line when telemetry_level != 'full'."""
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()
    cache = _make_cache(feature_enabled=True, feature={"telemetry_level": "shadow"})

    mock_resp = _make_resp({"chunks": [], "retrieval_bypassed": False})
    secret_query = "ZEER_GEHEIME_KLANTVRAAG_OVER_SALARIS"
    data = {
        "user": "aabbcc112233445566778899",
        "messages": [{"role": "user", "content": secret_query}],
    }

    with patch("klai_knowledge.httpx.AsyncClient") as cls:
        mc = AsyncMock()
        mc.post = AsyncMock(return_value=mock_resp)
        mc.get = AsyncMock(return_value=_make_resp({"enabled": True}))
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = mc

        with caplog.at_level(logging.WARNING, logger="klai_knowledge"):
            await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

    rendered = " ".join(record.getMessage() for record in caplog.records)
    # The metadata-only log line is allowed.
    assert "query_rewrite_metadata" in rendered or "query_rewrite" in rendered
    # The raw query MUST NOT have been logged.
    assert secret_query not in rendered, (
        "REQ-6 regression: raw query leaked into query_rewrite log in shadow mode"
    )


@pytest.mark.asyncio
async def test_query_rewrite_log_keeps_text_in_full_mode(monkeypatch, caplog):
    """Positive control: in 'full' mode, the raw query DOES appear in the
    log line (operators explicitly opted into raw-text retention)."""
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()
    cache = _make_cache(feature_enabled=True, feature={"telemetry_level": "full"})

    mock_resp = _make_resp({"chunks": [], "retrieval_bypassed": False})
    sample_query = "Hoe configureer ik de Salesforce-koppeling?"
    data = {
        "user": "aabbcc112233445566778899",
        "messages": [{"role": "user", "content": sample_query}],
    }

    with patch("klai_knowledge.httpx.AsyncClient") as cls:
        mc = AsyncMock()
        mc.post = AsyncMock(return_value=mock_resp)
        mc.get = AsyncMock(return_value=_make_resp({"enabled": True}))
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = mc

        with caplog.at_level(logging.WARNING, logger="klai_knowledge"):
            await hook.async_pre_call_hook(
                _make_user_api_key(), cache, data, "completion"
            )

    rendered = " ".join(record.getMessage() for record in caplog.records)
    # In 'full' mode the raw query is allowed in the log line. We assert
    # the canonical log shape rather than the literal query because the
    # rewriter may collapse / reshape the text.
    assert "query_rewrite" in rendered
    # Verify it's NOT the metadata-only variant (that's the redacted form).
    assert "query_rewrite_metadata" not in rendered
    rewrite_record = next(
        record
        for record in caplog.records
        if "query_rewrite org_id=" in record.getMessage()
    )
    assert rewrite_record.levelno == logging.WARNING


@pytest.mark.asyncio
async def test_shadow_decision_events_reach_warning_log_level(monkeypatch, caplog):
    """SPEC-RAG-SOURCE-SELECTION-001 REQ-1: decision events emitted on the
    pasted-correspondence path must survive the production WARNING filter.

    Shadow telemetry intentionally exercises the metadata-only rewrite event
    so the observability change cannot widen raw query logging.
    """
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()
    cache = _make_cache(feature_enabled=True, feature={"telemetry_level": "shadow"})
    pasted_query = (
        "Van: Klant <klant@example.nl>\n"
        "Aan: Support <support@example.nl>\n"
        "Onderwerp: storing\n\n"
        "Waarom geeft de trunk een 404 Not Found?"
    )
    data = {
        "user": "aabbcc112233445566778899",
        "messages": [{"role": "user", "content": pasted_query}],
    }
    mock_resp = _make_resp({"chunks": [], "retrieval_bypassed": False})

    with patch("klai_knowledge.httpx.AsyncClient") as cls:
        mc = AsyncMock()
        mc.post = AsyncMock(return_value=mock_resp)
        mc.get = AsyncMock(return_value=_make_resp({"enabled": True}))
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        cls.return_value = mc

        caplog.set_level(logging.WARNING, logger="klai_knowledge")
        await hook.async_pre_call_hook(
            _make_user_api_key(), cache, data, "completion"
        )

    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "klai_knowledge" and record.levelno >= logging.WARNING
    ]
    rendered = " ".join(warning_messages)
    assert "pasted_correspondence_detected org_id=" in rendered
    assert "query_rewrite_metadata org_id=" in rendered
    assert "sub_question_split_skipped_pasted_correspondence org_id=" in rendered
    assert "klant@example.nl" not in rendered
    assert "support@example.nl" not in rendered
    assert "Waarom geeft de trunk een 404 Not Found?" not in rendered
    assert pasted_query not in rendered


@pytest.mark.asyncio
async def test_kb_injection_summary_reaches_warning_log_level(monkeypatch, caplog):
    """SPEC-RAG-SOURCE-SELECTION-001 REQ-1: the post-call KB injection
    summary must survive the production WARNING filter.
    """
    mod = _load_hook(monkeypatch)
    hook = mod.KlaiKnowledgeHook()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Gebruik de handleiding.")
            )
        ]
    )
    data = {
        "metadata": {
            "_klai_kb_meta": {
                "org_id": "org123",
                "user_id": "user123",
                "chunks_injected": 1,
                "retrieval_ms": 12,
                "gate_bypassed": False,
                "allowed_source_urls": ["https://docs.getklai.com/handleiding"],
                "allowed_image_urls": [],
                "trusted_sources": [
                    {
                        "label": "1",
                        "title": "Handleiding",
                        "url": "https://docs.getklai.com/handleiding",
                    }
                ],
                "citation_chunks": [
                    {
                        "title": "Handleiding",
                        "source_url": "https://docs.getklai.com/handleiding",
                        "text": "Gebruik de handleiding.",
                    }
                ],
            }
        }
    }

    caplog.set_level(logging.WARNING, logger="klai_knowledge")
    await hook.async_post_call_success_hook(data, None, response)

    summary_record = next(
        (
            record
            for record in caplog.records
            if record.getMessage().startswith("KB injection: org=")
        ),
        None,
    )
    assert summary_record is not None
    assert summary_record.levelno == logging.WARNING
