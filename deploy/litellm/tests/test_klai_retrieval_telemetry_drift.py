"""Detect drift between vendored ``deploy/litellm/klai_retrieval_telemetry.py``
and the canonical ``klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_emit.py``.

SPEC-MCP-RETRIEVAL-001 Phase 1. The vendored copy exists because the LiteLLM
container is a stock upstream image without a path-dep mechanism, mirroring
the pattern from ``klai_chat_prompts.py``.

Phase D plan: replace the vendored file with a proper ``pip install`` of
``klai-retrieval-telemetry`` in a custom litellm Dockerfile, and delete this
test alongside the vendored module.

Implementation note
-------------------

Python's import system deduplicates by module name, so we cannot ``import
klai_retrieval_telemetry`` once for the vendored copy and once for the
canonical package and expect to get two different namespaces. We use
explicit ``importlib.util.spec_from_file_location`` to load each file under
a unique synthetic name (``_drift_vendored_telemetry``,
``_drift_canonical_telemetry``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = (
    _REPO_ROOT
    / "klai-libs"
    / "retrieval-telemetry"
    / "klai_retrieval_telemetry"
    / "_emit.py"
)
_VENDORED_PATH = _REPO_ROOT / "deploy" / "litellm" / "klai_retrieval_telemetry.py"


def _load(name: str, path: Path) -> ModuleType:
    """Load ``path`` as a fresh module under ``name`` (no sys.path dedup).

    Note: we register the module in ``sys.modules`` BEFORE ``exec_module``
    because Python 3.12+ dataclass decorators look up ``cls.__module__`` in
    sys.modules during class processing. Without the pre-registration,
    ``@dataclass(frozen=True, slots=True)`` on a class defined inside the
    loaded file raises ``AttributeError: 'NoneType' object has no attribute
    '__dict__'`` at import time.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"could not build module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


def test_vendored_source_is_byte_identical_to_canonical() -> None:
    """The vendored single-file copy MUST be byte-identical to the canonical
    ``_emit.py``. Drift here would mean LibreChat path (LiteLLM hook) and
    knowledge-mcp path emit different telemetry payload shapes — the exact
    failure mode SPEC-MCP-RETRIEVAL-001 Phase 1 prevents.
    """
    vendored_text = _VENDORED_PATH.read_text(encoding="utf-8")
    canonical_text = _CANONICAL_PATH.read_text(encoding="utf-8")
    assert vendored_text == canonical_text, (
        "Source drift between vendored and canonical klai_retrieval_telemetry.\n"
        f"  Update {_VENDORED_PATH.relative_to(_REPO_ROOT)} to match "
        f"{_CANONICAL_PATH.relative_to(_REPO_ROOT)}.\n"
        "  Easiest: cp <canonical> <vendored>"
    )


def test_vendored_public_api_matches_canonical() -> None:
    """Public function/class names on the vendored module MUST match the
    canonical module. Catches accidental refactors that delete a function
    only in one place.
    """
    vendored = _load("_drift_vendored_telemetry", _VENDORED_PATH)
    canonical = _load("_drift_canonical_telemetry", _CANONICAL_PATH)

    vendored_public = {n for n in dir(vendored) if not n.startswith("_")}
    canonical_public = {n for n in dir(canonical) if not n.startswith("_")}

    # Drop module-level imports that aren't part of the public surface
    # (httpx, asyncio, etc. land in dir() but are not what we mean by
    # "public API"). We pin only the symbols defined in this file.
    expected = {
        "RetrievalTelemetryConfig",
        "classify_gap",
        "fire_gap_event",
        "fire_retrieval_log",
    }
    assert expected.issubset(vendored_public)
    assert expected.issubset(canonical_public)
    assert vendored_public == canonical_public, (
        "Public-symbol drift between vendored and canonical telemetry.\n"
        f"  vendored - canonical: {vendored_public - canonical_public}\n"
        f"  canonical - vendored: {canonical_public - vendored_public}\n"
        f"  Update deploy/litellm/klai_retrieval_telemetry.py to match "
        f"klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_emit.py."
    )


def test_vendored_classify_gap_pure_function_signature() -> None:
    """``classify_gap`` must remain a pure function — same signature on both."""
    import inspect

    vendored = _load("_drift_vendored_telemetry_sig", _VENDORED_PATH)
    canonical = _load("_drift_canonical_telemetry_sig", _CANONICAL_PATH)

    v_sig = inspect.signature(vendored.classify_gap)
    c_sig = inspect.signature(canonical.classify_gap)
    assert v_sig.parameters.keys() == c_sig.parameters.keys()


def test_vendored_fire_helpers_accept_caller_client_id_kwarg() -> None:
    """REQ-9: both ``fire_*`` helpers MUST expose ``caller_client_id`` as a
    keyword argument so MCP-tool callers can label telemetry without a
    positional-argument refactor."""
    import inspect

    vendored = _load("_drift_vendored_telemetry_kwargs", _VENDORED_PATH)
    for helper_name in ("fire_retrieval_log", "fire_gap_event"):
        helper = getattr(vendored, helper_name)
        params = inspect.signature(helper).parameters
        assert "caller_client_id" in params, f"{helper_name} missing caller_client_id"
        assert params["caller_client_id"].kind == inspect.Parameter.KEYWORD_ONLY, (
            f"{helper_name}.caller_client_id must be keyword-only"
        )
