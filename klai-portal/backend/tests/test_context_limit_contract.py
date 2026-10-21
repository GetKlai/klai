"""Cross-deployable drift guard for the retrieval conversation/query content
character limit.

The 7800-vs-8000 contract lives in two separate deployables with no shared
import path between them:

- ``klai-portal/backend/app/services/partner_chat.py``
  (``_RETRIEVAL_HISTORY_CONTENT_MAX_CHARS = 7800``) — clips conversation
  history entries and the retrieval query before calling retrieval-api.
- ``klai-retrieval-api/retrieval_api/models.py``
  (``_CONVERSATION_CONTENT_MAX_CHARS = 8_000``) — the hard 422-reject limit
  enforced server-side on every ``conversation_history`` entry.

Per the repo pitfall "url-shape-multi-file-drift": any contract defined in
more than one file will silently drift unless a mechanical guard exists. This
test reads the OTHER deployable's source directly (it is not a Python
package importable from portal-api's venv — different service, different
dependency set) and regex-extracts its constant, anchored to the exact
assignment line so a rename or reshape fails this test loudly instead of
silently passing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.chat_prompting import _RETRIEVAL_HISTORY_CONTENT_MAX_CHARS

_RETRIEVAL_API_RELATIVE_PATH = "klai-retrieval-api/retrieval_api/models.py"

# Anchored to the exact assignment line in klai-retrieval-api/retrieval_api/models.py:
#   _CONVERSATION_CONTENT_MAX_CHARS = 8_000
_RETRIEVAL_API_HARD_LIMIT_RE = re.compile(
    r"^_CONVERSATION_CONTENT_MAX_CHARS\s*=\s*([\d_]+)\s*$",
    re.MULTILINE,
)


def _find_monorepo_root() -> Path | None:
    """Walk up from this test file until the sibling deployable is found.

    Returns None (rather than raising) when only a partial checkout is
    present, so the test can skip with a clear reason instead of failing
    for an unrelated environment reason.
    """
    current = Path(__file__).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / _RETRIEVAL_API_RELATIVE_PATH).is_file():
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
            "klai-retrieval-api not present in this checkout (partial "
            "checkout) — skipping cross-deployable drift contract test."
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
