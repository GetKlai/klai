"""Detect drift between LiteLLM-vendored and canonical klai_llm_safety."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_DIR = _REPO_ROOT / "klai-libs" / "llm-safety" / "klai_llm_safety"
_VENDORED_DIR = _REPO_ROOT / "deploy" / "litellm" / "klai_llm_safety"


def _relative_python_files(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*.py"))


def test_litellm_vendored_llm_safety_matches_canonical() -> None:
    canonical_files = _relative_python_files(_CANONICAL_DIR)
    vendored_files = _relative_python_files(_VENDORED_DIR)

    assert vendored_files == canonical_files
    for relative_path in canonical_files:
        assert (_VENDORED_DIR / relative_path).read_text() == (
            _CANONICAL_DIR / relative_path
        ).read_text()
