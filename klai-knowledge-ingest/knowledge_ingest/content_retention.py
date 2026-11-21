"""How much of a stored document's text a new version still carries.

Shared by the crawl run audit (adapters/crawler.py) and the per-document hold
in ingest_document, so both judge a replacement by the same measure that the
production history of 2026-11-12 was measured with.
"""

from __future__ import annotations

import re
from collections import Counter

_WORD = re.compile(r"\w{3,}")


def retention(stored: str, new: str) -> tuple[float, int]:
    """Return (retained, missing): the share and the number of stored words still/no longer present.

    Words are ``\\w{3,}``, lower-cased, compared as a multiset, so a word the
    stored version had three times counts as kept only as often as the new
    version repeats it. A stored text without words retains everything.
    """
    old = Counter(_WORD.findall(stored.lower()))
    total = sum(old.values())
    if not total:
        return 1.0, 0
    fresh = Counter(_WORD.findall(new.lower()))
    kept = sum(min(count, fresh[word]) for word, count in old.items())
    return kept / total, total - kept
