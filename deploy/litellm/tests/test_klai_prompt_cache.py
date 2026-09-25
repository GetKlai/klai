"""Tests for klai_prompt_cache.py.

Two tiers, like test_litellm_mistral_failover.py:

1. Fast unit tests against a stubbed `litellm` module (the hook's own
   derivation logic: stable prefix in, cache key out).
2. A drift guard against the REAL installed litellm package, pinned to the
   version the production image runs. This is the "record the verified
   version in a test, not a docstring" gate from AGENTS.md: klai_prompt_cache
   relies on a specific, non-obvious behavior of litellm==1.96.2's
   completion() kwarg forwarding (see the module docstring) that a routine
   litellm bump could silently change in either direction -- finally wiring
   `prompt_cache_key` into `completion()` (harmless) or closing the
   `extra_body` passthrough entirely (would break caching with no visible
   error, since drop_params swallows the parameter silently).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import re
import sys
import types
from pathlib import Path

import pytest
import yaml

_DEPLOY_DIR = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _DEPLOY_DIR / "litellm" / "config.yaml"
_COMPOSE_PATH = _DEPLOY_DIR / "docker-compose.yml"


@contextlib.contextmanager
def _stub_litellm():
    """Stub litellm module so klai_prompt_cache imports without the real package.

    A plain context manager, not an autouse fixture: this file also has
    module-scoped tests against the REAL litellm package below, and an
    autouse function-scoped stub would clobber sys.modules["litellm"]
    partway through those tests' lazy imports (which resolve
    sys.modules["litellm"].__path__ internally), not just around them.
    """
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    litellm_mod.integrations = integrations_mod
    integrations_mod.custom_logger = custom_logger_mod

    existing = {
        name: sys.modules.get(name)
        for name in ("litellm", "litellm.integrations", "litellm.integrations.custom_logger")
    }
    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod
    sys.modules.pop("klai_prompt_cache", None)

    try:
        yield
    finally:
        for name, module in existing.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        sys.modules.pop("klai_prompt_cache", None)


def _run_hook(data: dict, call_type: str = "completion"):
    with _stub_litellm():
        import klai_prompt_cache

        hook = klai_prompt_cache.PromptCacheKeyInjector()
        return asyncio.run(hook.async_pre_call_hook(None, None, data, call_type))


def _system_message(text: str) -> dict:
    return {"role": "system", "content": text}


_LONG_PREFIX_A = "You are a synthetic test assistant. " * 5
_LONG_PREFIX_B = "You are a different synthetic test assistant. " * 5


def test_pre_call_hook_sets_prompt_cache_key_in_extra_body_for_a_stable_prefix():
    """The outbound request carries the cache key -- under extra_body, not top-level.

    klai_prompt_cache.py's module docstring documents why: a top-level
    `data["prompt_cache_key"]` is silently dropped before it reaches Mistral
    on litellm==1.96.2 (completion()'s optional_param_args never forwards
    it), while `extra_body["prompt_cache_key"]` survives into the final
    request body.
    """
    data = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "hi"}],
    }
    out = _run_hook(data)

    assert "prompt_cache_key" not in out
    assert isinstance(out["extra_body"], dict)
    assert out["extra_body"]["prompt_cache_key"]


def test_same_model_and_stable_prefix_yields_the_same_cache_key():
    data1 = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "first question"}],
    }
    data2 = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "second, unrelated question"}],
    }
    out1 = _run_hook(data1)
    out2 = _run_hook(data2)

    assert out1["extra_body"]["prompt_cache_key"] == out2["extra_body"]["prompt_cache_key"]


def test_different_stable_prefix_yields_a_different_cache_key():
    data_a = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "hi"}],
    }
    data_b = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_B), {"role": "user", "content": "hi"}],
    }
    out_a = _run_hook(data_a)
    out_b = _run_hook(data_b)

    assert out_a["extra_body"]["prompt_cache_key"] != out_b["extra_body"]["prompt_cache_key"]


def test_different_final_model_yields_a_different_cache_key():
    """Matches production: a Mistral cache hit does not cross models (measured 2026-09-25)."""
    data_fast = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "hi"}],
    }
    data_medium = {
        "model": "klai-medium",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "hi"}],
    }
    out_fast = _run_hook(data_fast)
    out_medium = _run_hook(data_medium)

    assert out_fast["extra_body"]["prompt_cache_key"] != out_medium["extra_body"]["prompt_cache_key"]


def test_no_system_message_leaves_data_untouched():
    data = {"model": "klai-fast", "messages": [{"role": "user", "content": "hi"}]}
    out = _run_hook(data)

    assert "extra_body" not in out
    assert "prompt_cache_key" not in out


def test_non_completion_call_types_are_untouched():
    data = {
        "model": "klai-bge-m3",
        "messages": [_system_message(_LONG_PREFIX_A)],
    }
    out = _run_hook(data, call_type="embedding")

    assert "extra_body" not in out


def test_does_not_clobber_a_caller_supplied_non_dict_extra_body():
    data = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "hi"}],
        "extra_body": "not-a-dict",
    }
    out = _run_hook(data)

    assert out["extra_body"] == "not-a-dict"


def test_preserves_existing_extra_body_keys():
    data = {
        "model": "klai-fast",
        "messages": [_system_message(_LONG_PREFIX_A), {"role": "user", "content": "hi"}],
        "extra_body": {"random_seed": 7},
    }
    out = _run_hook(data)

    assert out["extra_body"]["random_seed"] == 7
    assert out["extra_body"]["prompt_cache_key"]


def test_list_content_system_message_is_flattened_like_custom_router_does():
    data = {
        "model": "klai-fast",
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": _LONG_PREFIX_A}]},
            {"role": "user", "content": "hi"},
        ],
    }
    out = _run_hook(data)

    assert out["extra_body"]["prompt_cache_key"]


# --- Drift guard against the real, pinned litellm package -----------------


@pytest.fixture(scope="module")
def real_litellm():
    """Temporarily replace the stubbed litellm with the real, pinned package."""
    existing_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "litellm" or name.startswith("litellm.")
    }
    for name in existing_modules:
        sys.modules.pop(name, None)
    try:
        module = importlib.import_module("litellm")
        yield module
    finally:
        for name in list(sys.modules):
            if name == "litellm" or name.startswith("litellm."):
                sys.modules.pop(name, None)
        sys.modules.update(existing_modules)


def test_behavior_runs_against_runtime_pinned_litellm_version(real_litellm) -> None:
    from importlib.metadata import version

    compose = yaml.safe_load(_COMPOSE_PATH.read_text())
    image = compose["services"]["litellm"]["image"]
    image_match = re.fullmatch(r".+/litellm:v(?P<version>[0-9][^@/\s]*)", image)
    assert image_match is not None, f"unsupported LiteLLM image reference: {image!r}"

    assert version("litellm") == image_match.group("version")


def test_top_level_prompt_cache_key_is_silently_dropped_for_mistral_under_drop_params(
    real_litellm,
) -> None:
    """Pins the exact drop_params.yaml-config behavior this module works around.

    config.yaml sets `litellm_settings.drop_params: true`, so a bare
    top-level `prompt_cache_key` is silently absent from optional_params
    rather than raising -- no error, no log line, the parameter just never
    reaches Mistral. (Without drop_params, get_optional_params raises
    UnsupportedParamsError instead of dropping silently; production runs
    with drop_params true, so silent loss is the relevant failure mode.)
    MistralConfig().get_supported_openai_params() does not list
    `prompt_cache_key`, and completion()'s optional_param_args never
    forwards it to get_optional_params for ANY provider (it is excluded
    from get_non_default_completion_params' catch-all precisely because it
    is a recognized OPENAI_CHAT_COMPLETION_PARAMS name). If a future
    litellm version wires it in, this goes red and klai_prompt_cache.py's
    extra_body workaround can be retired.
    """
    optional_params = real_litellm.get_optional_params(
        model="mistral-small-2603",
        custom_llm_provider="mistral",
        messages=[{"role": "system", "content": "x" * 100}, {"role": "user", "content": "hi"}],
        prompt_cache_key="unit-test-key",
        drop_params=True,
    )

    assert "prompt_cache_key" not in optional_params


def test_extra_body_prompt_cache_key_reaches_the_outbound_mistral_request_body(
    real_litellm,
) -> None:
    """The mechanism klai_prompt_cache.py actually uses, proven end to end.

    Captures the literal outbound HTTP JSON body for a mocked
    litellm.Router.acompletion call -- the same call path the LiteLLM proxy
    itself uses -- and asserts `prompt_cache_key` is present at the TOP
    LEVEL of that body, matching Mistral's documented request schema
    (docs.mistral.ai/api/endpoint/chat).
    """
    import litellm.llms.custom_httpx.http_handler as hh

    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = json.dumps(
            {
                "id": "x",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
        headers: dict = {}

        def json(self):
            return json.loads(self.text)

    async def fake_post(self, *args, **kwargs):
        captured["json"] = kwargs.get("json") or kwargs.get("data")
        return FakeResponse()

    original_post = hh.AsyncHTTPHandler.post
    hh.AsyncHTTPHandler.post = fake_post
    try:
        router = real_litellm.Router(
            model_list=[
                {
                    "model_name": "klai-fast",
                    "litellm_params": {"model": "mistral/mistral-small-2603", "api_key": "fake-key"},
                }
            ],
        )
        data = {
            "model": "klai-fast",
            "messages": [
                {"role": "system", "content": "x" * 100},
                {"role": "user", "content": "hi"},
            ],
            "extra_body": {"prompt_cache_key": "unit-test-key-router"},
        }
        asyncio.run(router.acompletion(**data))
    finally:
        hh.AsyncHTTPHandler.post = original_post

    body = json.loads(captured["json"])
    assert body["prompt_cache_key"] == "unit-test-key-router"
