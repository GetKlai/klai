"""Shared slug utility.

Converts a free-text name into a URL-safe slug following Klai's conventions:
- lowercase
- strip leading/trailing whitespace
- drop punctuation except hyphens and word characters
- collapse runs of whitespace/hyphens into a single hyphen
- trim to 64 characters

Returns an empty string for inputs that collapse to nothing. Callers MUST
reject empty-string slugs with HTTP 400 (see SPEC-CHAT-TEMPLATES-001
REQ-TEMPLATES-CRUD-U2).

Extracted from the inline `_slugify` helpers in `app/api/app_rules.py` and
`app/api/app_templates.py` (Jantine's `feat/chat-first-redesign`) — same
semantics, single source of truth.
"""

from __future__ import annotations

import re

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]")
_SLUG_COLLAPSE_RE = re.compile(r"[-\s]+")

MAX_SLUG_LENGTH = 64


def slugify(name: str) -> str:
    """Convert a name to a URL-safe slug.

    Deterministic: same input always produces the same slug. Unicode word
    characters are preserved (via `\\w` + `re.UNICODE` default in Python 3).

    Empty input or input that collapses to an empty string returns `""`.
    """
    if not name:
        return ""
    lowered = name.lower().strip()
    stripped = _SLUG_STRIP_RE.sub("", lowered)
    collapsed = _SLUG_COLLAPSE_RE.sub("-", stripped).strip("-")
    return collapsed[:MAX_SLUG_LENGTH]
