"""Cross-deployable drift guard for the retrieval conversation/query content
character limit.

The 7800-vs-8000 contract lives in three separate deployables with no shared
import path between them:

- ``klai-portal/backend/app/services/partner_chat.py``
  (``_RETRIEVAL_HISTORY_CONTENT_MAX_CHARS = 7800``) — clips conversation
  history entries and the retrieval query before calling retrieval-api.
- ``deploy/litellm/klai_kb_request_context.py``
  (``RETRIEVE_HISTORY_MAX_CONTENT_CHARS``, env-overridable, default 7800) —
  the LiteLLM-hook mirror of the same clipping logic for LibreChat traffic.
- ``klai-retrieval-api/retrieval_api/models.py``
  (``_CONVERSATION_CONTENT_MAX_CHARS = 8_000``) — the hard 422-reject limit
  enforced server-side on every ``conversation_history`` entry.

Per the repo pitfall "url-shape-multi-file-drift": any contract defined in
more than one file will silently drift unless a mechanical guard exists. This
test reads the two OTHER deployables' source directly (they are not Python
packages importable from portal-api's venv — different services, different
dependency sets) and regex-extracts their constants, anchored to the exact
assignment lines so a rename or reshape fails this test loudly instead of
silently passing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.partner_chat import _RETRIEVAL_HISTORY_CONTENT_MAX_CHARS

_RETRIEVAL_API_RELATIVE_PATH = "klai-retrieval-api/retrieval_api/models.py"
_LITELLM_HOOK_RELATIVE_PATH = "deploy/litellm/klai_kb_request_context.py"

# Anchored to the exact assignment line in klai-retrieval-api/retrieval_api/models.py:
#   _CONVERSATION_CONTENT_MAX_CHARS = 8_000
_RETRIEVAL_API_HARD_LIMIT_RE = re.compile(
    r"^_CONVERSATION_CONTENT_MAX_CHARS\s*=\s*([\d_]+)\s*$",
    re.MULTILINE,
)

# Anchored to the exact os.getenv default in deploy/litellm/klai_kb_request_context.py:
#   RETRIEVE_HISTORY_MAX_CONTENT_CHARS = min(
#       int(os.getenv("KNOWLEDGE_RETRIEVE_HISTORY_MAX_CONTENT_CHARS", "7800")),
_LITELLM_DEFAULT_RE = re.compile(
    r'int\(os\.getenv\("KNOWLEDGE_RETRIEVE_HISTORY_MAX_CONTENT_CHARS",\s*"(\d+)"\)\)',
)


def _find_monorepo_root() -> Path | None:
    """Walk up from this test file until both sibling deployables are found.

    Returns None (rather than raising) when only a partial checkout is
    present, so the test can skip with a clear reason instead of failing
    for an unrelated environment reason.
    """
    current = Path(__file__).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / _RETRIEVAL_API_RELATIVE_PATH).is_file() and (candidate / _LITELLM_HOOK_RELATIVE_PATH).is_file():
            return candidate
    return None


def _read_int_constant(pattern: re.Pattern[str], path: Path, *, description: str) -> int:
    text = path.read_text(encoding="utf-8")
    match = pattern.search(text)
    if not match:
        pytest.fail(
            f"Could not find {description} in {path} — the assignment line "
            "shape changed. Update the anchored regex in "
            "test_context_limit_contract.py alongside the source change."
        )
    return int(match.group(1).replace("_", ""))


@pytest.fixture(scope="module")
def monorepo_root() -> Path:
    root = _find_monorepo_root()
    if root is None:
        pytest.skip(
            "klai-retrieval-api and/or deploy/litellm not present in this "
            "checkout (partial checkout) — skipping cross-deployable drift "
            "contract test."
        )
    return root


def test_portal_clip_stays_below_retrieval_api_hard_limit(monorepo_root: Path) -> None:
    """Portal's clip threshold MUST stay strictly below retrieval-api's hard
    422-reject limit, or every clipped query/history entry would still be
    rejected by retrieval-api."""
    retrieval_api_hard_limit = _read_int_constant(
        _RETRIEVAL_API_HARD_LIMIT_RE,
        monorepo_root / _RETRIEVAL_API_RELATIVE_PATH,
        description="_CONVERSATION_CONTENT_MAX_CHARS",
    )

    assert _RETRIEVAL_HISTORY_CONTENT_MAX_CHARS < retrieval_api_hard_limit, (
        f"portal's _RETRIEVAL_HISTORY_CONTENT_MAX_CHARS "
        f"({_RETRIEVAL_HISTORY_CONTENT_MAX_CHARS}) must stay strictly below "
        f"retrieval-api's _CONVERSATION_CONTENT_MAX_CHARS "
        f"({retrieval_api_hard_limit}), or clipped content would still 422."
    )


def test_litellm_default_matches_portal_constant(monorepo_root: Path) -> None:
    """The LiteLLM-hook default (env-overridable, used for LibreChat traffic)
    must equal portal's constant (used for partner-API traffic), so both
    paths through retrieval-api clip at the same threshold."""
    litellm_default = _read_int_constant(
        _LITELLM_DEFAULT_RE,
        monorepo_root / _LITELLM_HOOK_RELATIVE_PATH,
        description="KNOWLEDGE_RETRIEVE_HISTORY_MAX_CONTENT_CHARS default",
    )

    assert litellm_default == _RETRIEVAL_HISTORY_CONTENT_MAX_CHARS, (
        f"deploy/litellm/klai_kb_request_context.py default "
        f"({litellm_default}) has drifted from portal's "
        f"_RETRIEVAL_HISTORY_CONTENT_MAX_CHARS "
        f"({_RETRIEVAL_HISTORY_CONTENT_MAX_CHARS}) — update one to match "
        "the other."
    )
