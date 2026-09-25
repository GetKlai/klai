"""Behavioral coverage for ordered Mistral-key failover in LiteLLM."""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

_DEPLOY_DIR = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _DEPLOY_DIR / "litellm" / "config.yaml"
_COMPOSE_PATH = _DEPLOY_DIR / "docker-compose.yml"
_ALIAS = "klai-medium"
_PRIMARY_ALIAS = "klai-primary"
_KLAI_KEY = "klai-test-key"
_KLAI2_KEY = "klai2-test-key"
_KEY_BY_ORDER = {1: _KLAI_KEY, 2: _KLAI2_KEY}
_TEXT_ALIASES = {"klai-primary", "klai-fast", "klai-large", "klai-medium"}


@pytest.fixture(scope="module")
def real_litellm():
    """Temporarily replace older test modules' LiteLLM stubs with the pinned package."""
    existing_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "litellm" or name.startswith("litellm.")
    }
    for name in existing_modules:
        sys.modules.pop(name, None)
    sys.modules.pop("klai_mistral_pool", None)
    try:
        module = importlib.import_module("litellm")
        # Imported here (once, for the whole module), not lazily per-test:
        # klai_mistral_pool.KlaiMistralPoolHook must inherit from THIS same
        # freshly-imported litellm's CustomLogger, the one the router below
        # actually runs isinstance() checks against -- a stale/stubbed
        # CustomLogger from an earlier test file would make the router
        # silently skip the hook. Keeping the import (and its resulting
        # class object) stable across every test in this module also keeps
        # _pinned_router's remove_callbacks_by_type/add_litellm_callback
        # dance correct between tests (see _pinned_router).
        importlib.import_module("klai_mistral_pool")
        yield module
    finally:
        for name in list(sys.modules):
            if name == "litellm" or name.startswith("litellm."):
                sys.modules.pop(name, None)
        sys.modules.update(existing_modules)
        sys.modules.pop("klai_mistral_pool", None)


def _pinned_router(real_litellm: Any, *, rpm_overrides: dict[tuple[str, int], int] | None = None):
    import klai_mistral_pool

    config = yaml.safe_load(_CONFIG_PATH.read_text())
    deployments = [
        entry for entry in config["model_list"] if entry["model_name"] in _TEXT_ALIASES
    ]
    for deployment in deployments:
        order = deployment["litellm_params"]["order"]
        deployment["litellm_params"]["api_key"] = _KEY_BY_ORDER[order]
        if rpm_overrides and (deployment["model_name"], order) in rpm_overrides:
            deployment["rpm"] = rpm_overrides[(deployment["model_name"], order)]
            # Router._generate_model_id hashes litellm_params (model, api_key,
            # order), not the top-level rpm/tpm fields, so a plain rpm change
            # alone would still resolve to the SAME deployment id -- and the
            # same rpm/tpm usage counters -- as every other test using this
            # api_key/order/model combination. Suffixing the key gives this
            # deployment its own id, so its usage count starts fresh.
            deployment["litellm_params"]["api_key"] += "-rpm-test"
    real_litellm.num_retries = config["litellm_settings"]["num_retries"]

    # Fresh hook instance per test: a previous test's instance (if any) is
    # removed first so FULL state never leaks between tests -- litellm's own
    # callback dedup keys purely on class name, so an add without a prior
    # remove would silently keep reusing the old instance.
    hook = klai_mistral_pool.KlaiMistralPoolHook()
    real_litellm.logging_callback_manager.remove_callbacks_by_type(
        real_litellm.callbacks, klai_mistral_pool.KlaiMistralPoolHook
    )
    real_litellm.logging_callback_manager.add_litellm_callback(hook)

    router = real_litellm.Router(
        model_list=deployments,
        **config["router_settings"],
    )
    return router, hook


def _response(real_litellm: Any, model: str):
    return real_litellm.ModelResponse(
        model=model,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": "backup response"},
                "finish_reason": "stop",
            }
        ],
    )


def test_behavior_runs_against_runtime_pinned_litellm_version() -> None:
    compose = yaml.safe_load(_COMPOSE_PATH.read_text())
    image = compose["services"]["litellm"]["image"]
    image_match = re.fullmatch(r".+/litellm:v(?P<version>[0-9][^@/\s]*)", image)

    assert image_match is not None, f"unsupported LiteLLM image reference: {image!r}"
    pinned_version = image_match.group("version")

    assert version("litellm") == pinned_version


@pytest.mark.asyncio
async def test_healthy_primary_order_is_not_randomly_load_balanced(
    real_litellm,
) -> None:
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        return _response(real_litellm, kwargs["model"])

    router, _hook = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            for _ in range(8):
                await router.acompletion(
                    model=_PRIMARY_ALIAS,
                    messages=[{"role": "user", "content": "hello"}],
                )
    finally:
        router.reset()

    assert calls == [_KLAI_KEY] * 8


@pytest.mark.asyncio
async def test_primary_alias_falls_back_to_medium_without_spilling_to_order2(
    real_litellm,
) -> None:
    """A persistent 429 on klai-primary's order 1 must exhaust order 1 only
    (never order 2) before the cross-model fallback to klai-medium kicks in."""
    calls: list[tuple[str, str]] = []

    async def provider_completion(**kwargs):
        calls.append((kwargs["model"], kwargs["api_key"]))
        if kwargs["model"] == "mistral/mistral-small-2603":
            raise real_litellm.RateLimitError(
                "rate limited",
                llm_provider="mistral",
                model=kwargs["model"],
            )
        return _response(real_litellm, kwargs["model"])

    router, _hook = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            response = await router.acompletion(
                model=_PRIMARY_ALIAS,
                messages=[{"role": "user", "content": "hello"}],
            )
    finally:
        router.reset()

    assert response.choices[0].message.content == "backup response"
    # Order 1 exhausts its own retries, then the router's order-based
    # fallback to order 2 is redirected back to order 1 (never FULL, so
    # order 2 is never eligible), and only then does the cross-model
    # fallback to klai-medium (also order 1) take over. klai2-test-key never
    # appears.
    assert calls == [
        *[("mistral/mistral-small-2603", _KLAI_KEY)] * 8,
        ("mistral/mistral-medium-3.5", _KLAI_KEY),
    ]


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_on_the_same_account(
    real_litellm,
) -> None:
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == _KLAI_KEY and len(calls) == 1:
            raise real_litellm.RateLimitError(
                "rate limited",
                llm_provider="mistral",
                model=kwargs["model"],
            )
        return _response(real_litellm, kwargs["model"])

    router, _hook = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            response = await router.acompletion(
                model=_ALIAS,
                messages=[{"role": "user", "content": "hello"}],
            )
    finally:
        router.reset()

    assert response.choices[0].message.content == "backup response"
    assert calls == [_KLAI_KEY, _KLAI_KEY]


@pytest.mark.asyncio
async def test_a_full_klai_account_hands_over_to_klai2(
    real_litellm,
) -> None:
    """Mistral answers 402 once an organisation or workspace spending limit is hit."""
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == _KLAI_KEY:
            raise real_litellm.APIError(
                status_code=402,
                message="Workspace monthly spending limit reached",
                llm_provider="mistral",
                model=kwargs["model"],
            )
        return _response(real_litellm, kwargs["model"])

    router, _hook = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            response = await router.acompletion(
                model=_ALIAS,
                messages=[{"role": "user", "content": "hello"}],
            )
    finally:
        router.reset()

    assert response.choices[0].message.content == "backup response"
    # A 402 means the account is full: not retried, straight to the next account.
    assert calls == [_KLAI_KEY, _KLAI2_KEY]


@pytest.mark.asyncio
async def test_a_full_klai_account_is_skipped_on_the_next_request(
    real_litellm,
) -> None:
    """The account marked FULL by a 402 must not be tried again by a later,
    independent request -- only the request that hit the 402 pays for it."""
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == _KLAI_KEY:
            raise real_litellm.APIError(
                status_code=402,
                message="Workspace monthly spending limit reached",
                llm_provider="mistral",
                model=kwargs["model"],
            )
        return _response(real_litellm, kwargs["model"])

    router, _hook = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            await router.acompletion(model=_ALIAS, messages=[{"role": "user", "content": "hello"}])
            calls.clear()
            second = await router.acompletion(model=_ALIAS, messages=[{"role": "user", "content": "hello again"}])
    finally:
        router.reset()

    assert second.choices[0].message.content == "backup response"
    assert calls == [_KLAI2_KEY]


@pytest.mark.asyncio
async def test_persistent_rate_limit_on_order1_never_reaches_order2(real_litellm) -> None:
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        raise real_litellm.RateLimitError(
            "rate limited",
            llm_provider="mistral",
            model=kwargs["model"],
        )

    router, _hook = _pinned_router(real_litellm)
    try:
        with (
            patch("litellm.acompletion", side_effect=provider_completion),
            pytest.raises(real_litellm.RateLimitError, match="rate limited"),
        ):
            await asyncio.wait_for(
                router.acompletion(
                    model=_ALIAS,
                    messages=[{"role": "user", "content": "hello"}],
                ),
                timeout=10,
            )
    finally:
        router.reset()

    # Being busy is not being full: order 2 is never called, no matter how
    # many times order 1's retries are exhausted.
    assert calls == [_KLAI_KEY] * 8
    assert _KLAI2_KEY not in calls


@pytest.mark.asyncio
async def test_rpm_budget_exhaustion_on_order1_does_not_spill_to_order2(real_litellm) -> None:
    """LiteLLM's own enforce_model_rate_limits pre-call check (not the
    provider) raises the RateLimitError here -- a different code path than
    the provider-raised 429 above, exercised because it runs before
    async_filter_deployments's own order narrowing gets a chance to react
    to a fresh exception."""

    async def provider_completion(**kwargs):
        return _response(real_litellm, kwargs["model"])

    router, _hook = _pinned_router(real_litellm, rpm_overrides={(_ALIAS, 1): 1})
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            first = await router.acompletion(model=_ALIAS, messages=[{"role": "user", "content": "hello"}])
            assert first.choices[0].message.content == "backup response"

            calls: list[str] = []

            async def provider_completion_tracking(**kwargs):
                calls.append(kwargs["api_key"])
                return _response(real_litellm, kwargs["model"])

            with (
                patch("litellm.acompletion", side_effect=provider_completion_tracking),
                pytest.raises(real_litellm.RateLimitError),
            ):
                await asyncio.wait_for(
                    router.acompletion(model=_ALIAS, messages=[{"role": "user", "content": "hello"}]),
                    timeout=10,
                )
    finally:
        router.reset()

    assert _KLAI2_KEY not in calls


@pytest.mark.asyncio
async def test_full_account_is_re_admitted_after_bench_time(real_litellm) -> None:
    """After the bench time elapses, the next request probes order 1 again
    instead of staying on order 2 forever."""
    calls: list[str] = []
    order1_full = True

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == _KLAI_KEY and order1_full:
            raise real_litellm.APIError(
                status_code=402,
                message="Workspace monthly spending limit reached",
                llm_provider="mistral",
                model=kwargs["model"],
            )
        return _response(real_litellm, kwargs["model"])

    router, hook = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            await router.acompletion(model=_ALIAS, messages=[{"role": "user", "content": "hello"}])
            assert hook._is_full(1)
            order1_full = False  # simulate Mark raising the spending limit

            # Force the bench time to have already elapsed.
            hook._full_until[1] = time.monotonic() - 1

            calls.clear()
            second = await router.acompletion(model=_ALIAS, messages=[{"role": "user", "content": "hello again"}])
    finally:
        router.reset()

    assert second.choices[0].message.content == "backup response"
    assert calls == [_KLAI_KEY]
