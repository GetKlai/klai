"""Behavioral coverage for ordered Mistral-key failover in LiteLLM."""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
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
_PRO_KEY = "pro-test-key"
_PRIMARY_KEY = "primary-test-key"
_BACKUP_KEY = "backup-test-key"
_KEY_BY_ORDER = {1: _PRO_KEY, 2: _PRIMARY_KEY, 3: _BACKUP_KEY}
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
    try:
        module = importlib.import_module("litellm")
        yield module
    finally:
        for name in list(sys.modules):
            if name == "litellm" or name.startswith("litellm."):
                sys.modules.pop(name, None)
        sys.modules.update(existing_modules)


def _pinned_router(real_litellm: Any):
    config = yaml.safe_load(_CONFIG_PATH.read_text())
    deployments = [
        entry for entry in config["model_list"] if entry["model_name"] in _TEXT_ALIASES
    ]
    for deployment in deployments:
        order = deployment["litellm_params"]["order"]
        deployment["litellm_params"]["api_key"] = _KEY_BY_ORDER[order]
    real_litellm.num_retries = config["litellm_settings"]["num_retries"]
    return real_litellm.Router(
        model_list=deployments,
        **config["router_settings"],
    )


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

    router = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            for _ in range(8):
                await router.acompletion(
                    model=_PRIMARY_ALIAS,
                    messages=[{"role": "user", "content": "hello"}],
                )
    finally:
        router.reset()

    assert calls == [_PRO_KEY] * 8


@pytest.mark.asyncio
async def test_primary_alias_falls_back_to_medium_after_every_small_key_fails(
    real_litellm,
) -> None:
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

    router = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            response = await router.acompletion(
                model=_PRIMARY_ALIAS,
                messages=[{"role": "user", "content": "hello"}],
            )
    finally:
        router.reset()

    assert response.choices[0].message.content == "backup response"
    assert calls == [
        ("mistral/mistral-small-2603", _PRO_KEY),
        ("mistral/mistral-small-2603", _PRO_KEY),
        ("mistral/mistral-small-2603", _PRIMARY_KEY),
        ("mistral/mistral-small-2603", _PRIMARY_KEY),
        ("mistral/mistral-small-2603", _BACKUP_KEY),
        ("mistral/mistral-small-2603", _BACKUP_KEY),
        ("mistral/mistral-medium-3.5", _BACKUP_KEY),
    ]


@pytest.mark.asyncio
async def test_a_rate_limited_pro_organisation_hands_over_to_the_klai_organisation(
    real_litellm,
) -> None:
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == _PRO_KEY:
            raise real_litellm.RateLimitError(
                "rate limited",
                llm_provider="mistral",
                model=kwargs["model"],
            )
        return _response(real_litellm, kwargs["model"])

    router = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            response = await router.acompletion(
                model=_ALIAS,
                messages=[{"role": "user", "content": "hello"}],
            )
    finally:
        router.reset()

    assert response.choices[0].message.content == "backup response"
    assert calls == [_PRO_KEY, _PRO_KEY, _PRIMARY_KEY]


@pytest.mark.asyncio
async def test_organisations_over_their_monthly_limit_reach_pay_as_you_go_last(
    real_litellm,
) -> None:
    """Mistral answers 402 once an organisation or workspace spending limit is hit."""
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] in (_PRO_KEY, _PRIMARY_KEY):
            raise real_litellm.APIError(
                status_code=402,
                message="Workspace monthly spending limit reached",
                llm_provider="mistral",
                model=kwargs["model"],
            )
        return _response(real_litellm, kwargs["model"])

    router = _pinned_router(real_litellm)
    try:
        with patch("litellm.acompletion", side_effect=provider_completion):
            response = await router.acompletion(
                model=_ALIAS,
                messages=[{"role": "user", "content": "hello"}],
            )
    finally:
        router.reset()

    assert response.choices[0].message.content == "backup response"
    assert calls[-1] == _BACKUP_KEY
    assert calls.index(_PRIMARY_KEY) > calls.index(_PRO_KEY)
    assert calls.index(_BACKUP_KEY) > calls.index(_PRIMARY_KEY)


@pytest.mark.asyncio
async def test_every_key_exhausted_ends_in_a_bounded_terminal_error(real_litellm) -> None:
    calls: list[str] = []

    async def provider_completion(**kwargs):
        calls.append(kwargs["api_key"])
        raise real_litellm.RateLimitError(
            "rate limited",
            llm_provider="mistral",
            model=kwargs["model"],
        )

    router = _pinned_router(real_litellm)
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

    assert calls == [_PRO_KEY, _PRO_KEY, _PRIMARY_KEY, _PRIMARY_KEY] + [_BACKUP_KEY] * 4
