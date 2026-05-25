"""Detect drift between vendored LiteLLM citations and canonical klai-citations."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = _REPO_ROOT / "klai-libs" / "citations" / "klai_citations" / "__init__.py"
_VENDORED_PATH = _REPO_ROOT / "deploy" / "litellm" / "klai_citations.py"


def _load(name: str, path: Path) -> ModuleType:
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
    assert _VENDORED_PATH.read_text(encoding="utf-8") == _CANONICAL_PATH.read_text(encoding="utf-8")


def test_vendored_public_api_matches_canonical() -> None:
    vendored = _load("_drift_vendored_citations", _VENDORED_PATH)
    canonical = _load("_drift_canonical_citations", _CANONICAL_PATH)

    assert set(vendored.__all__) == set(canonical.__all__)
    for name in canonical.__all__:
        assert hasattr(vendored, name)
