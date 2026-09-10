"""Contract tests for the Klai proxy hooks against the REAL litellm package.

Every other hook test in this directory replaces
``litellm.integrations.custom_logger`` with a hand-written stub whose methods
accept ``*args, **kwargs``. CI does install the real ``litellm==1.96.2``, but
no test touches it — so a LiteLLM bump that changes a hook signature, or that
makes one of our overrides stop being an override, passes CI unnoticed and
lands in every LibreChat chat turn. LiteLLM went from v1.83.7 to v1.96.2 in
three months.

Read in a subprocess, like ``test_pii_delegation_role_drift.py`` and for the
same reason: sibling modules install a fake ``litellm`` into ``sys.modules``
at collection time, so an in-process import here would be order-dependent and
would leave the shared module table in a state the next test does not expect.

What this does NOT guard: that the last-registered callback produces the
OUTERMOST generator, i.e. that its output is what the user sees.
``klai_pii_enforce``'s docstring claims that; proving it means consuming
``ProxyLogging.async_post_call_streaming_iterator_hook``, which imports the
whole FastAPI proxy app on its first line. Measured 2026-09-10: reaching it
pulls in websockets, redis and the rest of the proxy stack, far more CI
surface than a contract test is worth. The registration order asserted below
is the input that loop consumes — if it flips, the wrapping flips with it.
That end-to-end check is the natural first customer for a real
integration-test tier.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_LITELLM_DIR = Path(__file__).resolve().parents[1]

# Runs against the real package. Mirrors what ProxyLogging itself does:
# it classifies a callback by looking in ``type(cb).__dict__``, so a hook that
# only inherits the base method is skipped entirely rather than called.
_PROBE = """
import inspect, json, os, sys
os.environ.setdefault("KNOWLEDGE_RETRIEVE_URL", "http://retrieval-api:8040/retrieve")
sys.path.insert(0, %r)

import litellm
from importlib.metadata import version
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.utils import ProxyLogging

import klai_knowledge
import klai_pii_enforce

STREAM = "async_post_call_streaming_iterator_hook"
HOOKS = ["async_pre_call_hook", "async_post_call_success_hook", STREAM,
         "async_post_call_failure_hook"]
CLASSES = {"KlaiKnowledgeHook": klai_knowledge.KlaiKnowledgeHook,
           "KlaiPiiEnforcer": klai_pii_enforce.KlaiPiiEnforcer}

out = {"litellm": version("litellm"), "signatures": {}, "overrides": {}}

for hook in HOOKS:
    base = inspect.signature(getattr(CustomLogger, hook))
    names = [p.name for p in base.parameters.values()
             if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.name != "self"]
    out["signatures"][hook] = {"base_parameters": names, "binds": {}}
    for label, cls in CLASSES.items():
        # Leaf-class registration, not getattr: an inherited base method must
        # not count as an override.
        own = cls.__dict__.get(hook)
        out["overrides"].setdefault(label, {})[hook] = own is not None
        target = own if own is not None else getattr(CustomLogger, hook)
        try:
            inspect.signature(target).bind(None, **{n: None for n in names})
            out["signatures"][hook]["binds"][label] = True
        except TypeError as exc:
            out["signatures"][hook]["binds"][label] = str(exc)

async def _passthrough(self, user_api_key_dict, response, request_data):
    async for chunk in response:
        yield chunk

Marker = type("Marker", (CustomLogger,), {STREAM: _passthrough})
first, second, plain = Marker(), Marker(), CustomLogger()
saved = list(litellm.callbacks)
litellm.callbacks[:] = [first, plain, second]
try:
    caps = ProxyLogging._callback_capabilities()   # staticmethod, no instance needed
    seen = [e[0] if isinstance(e, tuple) else getattr(e, "__self__", e)
            for e in caps.iterator_overrides]
    out["iterator_overrides"] = ["first" if s is first else
                                 "second" if s is second else
                                 "plain" if s is plain else repr(s) for s in seen]
finally:
    litellm.callbacks[:] = saved

print(json.dumps(out))
""" % str(_LITELLM_DIR)


@pytest.fixture(scope="module")
def contract() -> dict:
    """Everything the real litellm reports, gathered in one subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        cwd=_LITELLM_DIR,
    )
    assert result.returncode == 0, (
        "could not read the real litellm contract:\n" + result.stderr[-2000:]
    )
    return json.loads(result.stdout)


def test_hook_overrides_accept_the_real_customlogger_parameters(contract: dict) -> None:
    """Each override binds the base class's parameter names as keywords.

    Deliberately not a positional name comparison: klai_knowledge declares
    ``async_post_call_failure_hook(self, *args, **kwargs)``, which is
    call-compatible but has no matching names. Binding is what LiteLLM does.
    """
    version = contract["litellm"]
    for hook, detail in contract["signatures"].items():
        for label, outcome in detail["binds"].items():
            assert outcome is True, (
                f"litellm {version}: CustomLogger.{hook} passes "
                f"{detail['base_parameters']} as keywords, but {label}.{hook} "
                f"rejects them: {outcome}"
            )


def test_hooks_are_registered_on_the_leaf_class(contract: dict) -> None:
    """ProxyLogging classifies on ``type(cb).__dict__``, so inheriting is not enough.

    A callback that stops declaring the streaming hook itself is dropped from
    ``caps.iterator_overrides`` and simply never runs again — no error, no log
    line. The other three hooks are checked the same way so a quietly removed
    override cannot pass by inheriting a no-op base method.
    """
    version = contract["litellm"]
    for label, hooks in contract["overrides"].items():
        for hook, declared in hooks.items():
            assert declared, (
                f"litellm {version}: {label} no longer declares {hook} itself; "
                "LiteLLM looks in the leaf class's __dict__, so an inherited "
                "base method means this hook silently never runs"
            )


def test_iterator_overrides_keep_registration_order(contract: dict) -> None:
    """Registration order survives, and a non-overriding callback is dropped."""
    assert contract["iterator_overrides"] == ["first", "second"], (
        f"litellm {contract['litellm']}: caps.iterator_overrides="
        f"{contract['iterator_overrides']}; expected ['first', 'second'] — "
        "registration order preserved and the non-overriding callback absent"
    )
