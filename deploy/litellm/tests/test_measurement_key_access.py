"""The measurement key reaches the Vibe aliases and nothing else, and LiteLLM itself enforces that.

On 23 October 2026 measurement rounds on the master key spent both chat
workspaces' monthly Mistral limit and took chat down for every tenant.
deploy/scripts/litellm-measurement-key.sh now creates a virtual key whose
`models` list is klai-judge and klai-ingest, and the one-off scripts give a
clone no other LiteLLM credential. That only holds if the pinned LiteLLM
refuses every other alias on that key, so this drives the proxy's own check
(`auth_checks.can_key_call_model`, which user_api_key_auth runs on every LLM
route for the requested model and for each client-named fallback, before any
hook or router fallback) against a router built from config.yaml. A new alias, a router alias or a wildcard that would widen
the key turns this red. The live counterpart is the script's `--verify`.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

_DEPLOY_DIR = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _DEPLOY_DIR / "litellm" / "config.yaml"
_SCRIPT_PATH = _DEPLOY_DIR / "scripts" / "litellm-measurement-key.sh"
_HOOK_PATH = _DEPLOY_DIR / "litellm" / "klai_pii_enforce.py"
_VIBE_ALIASES = {"klai-judge", "klai-ingest"}


def _script_key_request() -> tuple[str, dict]:
    script = _SCRIPT_PATH.read_text()
    alias = re.search(r'^KEY_ALIAS="([^"]+)"$', script, re.M).group(1)
    body = re.search(r"^KEY_BODY='(.*)'$", script, re.M).group(1).replace("'\"$KEY_ALIAS\"'", alias)
    return alias, json.loads(body)


def _hook_alias() -> str:
    tree = ast.parse(_HOOK_PATH.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "MEASUREMENT_KEY_ALIAS" for t in node.targets
        ):
            return node.value.value
    raise AssertionError("klai_pii_enforce.py no longer defines MEASUREMENT_KEY_ALIAS")


@pytest.fixture(scope="module")
def litellm_proxy():
    """The pinned package, not a stub an earlier test module left in sys.modules."""
    stale = {name: mod for name, mod in sys.modules.items() if name == "litellm" or name.startswith("litellm.")}
    for name in stale:
        sys.modules.pop(name)
    try:
        litellm = importlib.import_module("litellm")
        auth = importlib.import_module("litellm.proxy.auth.auth_checks")
        types = importlib.import_module("litellm.proxy._types")
        yield litellm, auth, types
    finally:
        for name in [n for n in sys.modules if n == "litellm" or n.startswith("litellm.")]:
            sys.modules.pop(name)
        sys.modules.update(stale)


def _config_aliases() -> set[str]:
    return {entry["model_name"] for entry in yaml.safe_load(_CONFIG_PATH.read_text())["model_list"]}


def _router(litellm):
    """Built inside the test: litellm.Router schedules tasks, so it needs the running loop."""
    config = yaml.safe_load(_CONFIG_PATH.read_text())
    model_list = config["model_list"]
    for entry in model_list:
        entry["litellm_params"]["api_key"] = "test-key"
    return litellm.Router(model_list=model_list, fallbacks=config["router_settings"]["fallbacks"])


async def _check(litellm_proxy, model: str) -> None:
    litellm, auth, types = litellm_proxy
    router = _router(litellm)
    _, key_request = _script_key_request()
    token = types.UserAPIKeyAuth(
        api_key="sk-measurement-test",
        key_alias=key_request["key_alias"],
        models=key_request["models"],
    )
    await auth.can_key_call_model(
        model=model,
        llm_model_list=router.model_list,
        valid_token=token,
        llm_router=router,
    )


def test_the_script_asks_for_the_vibe_aliases_on_an_llm_only_key_with_its_own_daily_budget():
    alias, body = _script_key_request()

    assert set(body["models"]) == _VIBE_ALIASES
    assert body["key_type"] == "llm_api"
    assert body["max_budget"] > 0
    assert body["budget_duration"] == "1d"
    assert alias == _hook_alias()


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", sorted(_VIBE_ALIASES))
async def test_the_measurement_key_may_call_the_vibe_aliases(litellm_proxy, alias):
    await _check(litellm_proxy, alias)


@pytest.mark.asyncio
async def test_every_other_alias_in_the_config_is_refused_by_the_key_check(litellm_proxy):
    _, _, types = litellm_proxy
    others = sorted(_config_aliases() - _VIBE_ALIASES)
    assert {"klai-primary", "klai-fast", "klai-medium", "klai-large"} <= set(others)

    for alias in others:
        with pytest.raises(types.ProxyException) as refused:
            await _check(litellm_proxy, alias)
        assert refused.value.type == "key_model_access_denied", alias
        assert str(refused.value.code) == "403", alias

