"""Router upgrade for strict-mode KB answers with hallucination-risk signals.

Strict KB chat runs on klai-primary (mistral-small). When the message is
multi-part or the low-confidence guard fired AND the model still answers
(chunks_present), instruction-following quality is the difference between
per-question honesty and confident interpolation — those requests upgrade to
klai-medium. All other traffic keeps its existing routing.
"""

from __future__ import annotations

import importlib
import sys
import types
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
    litellm_mod.token_counter = MagicMock(return_value=10)

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
    sys.modules.pop("custom_router", None)


def _load_router():
    sys.modules.pop("custom_router", None)
    import custom_router

    importlib.reload(custom_router)
    return custom_router


def _kb_meta(**overrides):
    meta = {
        "kb_narrow": True,
        "answer_policy_state": "chunks_present",
        "multi_question": False,
        "low_confidence_inject": False,
    }
    meta.update(overrides)
    return meta


class TestKbRiskUpgradePredicate:
    def test_strict_multi_question_upgrades(self):
        router = _load_router()
        assert router._kb_risk_upgrade(_kb_meta(multi_question=True)) is True

    def test_strict_low_confidence_upgrades(self):
        router = _load_router()
        assert router._kb_risk_upgrade(_kb_meta(low_confidence_inject=True)) is True

    def test_strict_without_risk_signals_stays(self):
        router = _load_router()
        assert router._kb_risk_upgrade(_kb_meta()) is False

    def test_open_mode_never_upgrades(self):
        router = _load_router()
        assert (
            router._kb_risk_upgrade(
                _kb_meta(kb_narrow=False, multi_question=True, low_confidence_inject=True)
            )
            is False
        )

    def test_non_answering_states_never_upgrade(self):
        """Refusal/mock branches never reach the model; upgrading is waste."""
        router = _load_router()
        for state in ("zero_chunks", "retrieval_failure", "gate_bypassed"):
            assert (
                router._kb_risk_upgrade(
                    _kb_meta(answer_policy_state=state, multi_question=True)
                )
                is False
            )

    def test_malformed_meta_is_safe(self):
        router = _load_router()
        assert router._kb_risk_upgrade(None) is False
        assert router._kb_risk_upgrade("not-a-dict") is False
        assert router._kb_risk_upgrade({}) is False


class TestRouterIntegration:
    @pytest.mark.asyncio
    async def test_strict_risk_routes_to_klai_medium(self):
        router = _load_router()
        hook = router.TokenRouter()
        data = {
            "model": "klai-primary",
            "messages": [{"role": "user", "content": "Vraag een? Vraag twee?"}],
            "metadata": {
                "_klai_kb_meta": _kb_meta(multi_question=True),
            },
        }

        result = await hook.async_pre_call_hook(MagicMock(), MagicMock(), data, "completion")

        assert result["model"] == "klai-medium"
        assert result["metadata"]["_klai_router_meta"]["route_reason"] == "strict_kb_risk"

    @pytest.mark.asyncio
    async def test_kb_context_without_risk_keeps_primary(self):
        router = _load_router()
        hook = router.TokenRouter()
        data = {
            "model": "klai-primary",
            "messages": [{"role": "user", "content": "Wat is het verzuimprotocol?"}],
            "metadata": {
                "_klai_kb_meta": _kb_meta(),
            },
        }

        result = await hook.async_pre_call_hook(MagicMock(), MagicMock(), data, "completion")

        assert result["model"] == "klai-primary"
        assert result["metadata"]["_klai_router_meta"]["route_reason"] == "kb_context"
