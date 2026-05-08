"""SPEC-PRIVACY-QUERY-SHADOW-001 — symbolic-feature extraction for shadow store.

Produces a small jsonb-friendly dict from the user's query that captures
shape and intent without revealing literal text. Stored alongside the
1024-dim BGE-M3 embedding in telemetry.query_shadow (REQ-7).

The features set was scoped deliberately small to keep the privacy-leak
surface minimal:

- ``tokens`` — int, character-based token count (cheap, language-agnostic)
- ``lang`` — one of {"nl", "en", "other"}, very simple stoplist heuristic
- ``has_brand`` — bool, whether any common Klai-tenant brand-keyword appeared
- ``brand_count`` — int, how many brand-keywords matched
- ``question_word`` — bool, query starts with a Dutch/English question word
- ``has_url`` — bool, query contains an http(s):// URL
- ``has_email_pattern`` — bool, query contains an email-shaped substring

We purposely DO NOT extract topic vectors, NER spans, or any feature
that would make the shadow row uniquely traceable to a user.
"""

from __future__ import annotations

import re
from typing import Final

# Brand-keyword list is intentionally conservative — false positives are
# better than false negatives because the field only signals "the query
# mentions a brand", not which one. Future tenants extend via a
# settings-side list; keeping it as a hardcoded constant is fine for v1.
_BRAND_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "klai",
        "voys",
        "salesforce",
        "hubspot",
        "notion",
        "github",
        "google",
        "microsoft",
        "outlook",
        "gmail",
        "slack",
        "teams",
        "zoom",
        "stripe",
        "moneybird",
    }
)

# Lowercased question words — Dutch primary (Klai's default tenant
# language) plus English fallback. The list is intentionally short:
# we want the feature to fire on canonical interrogatives only.
_QUESTION_WORDS_NL: Final[frozenset[str]] = frozenset(
    {"hoe", "wat", "waarom", "wanneer", "waar", "wie", "welke", "kan", "is", "kunnen"}
)
_QUESTION_WORDS_EN: Final[frozenset[str]] = frozenset(
    {"how", "what", "why", "when", "where", "who", "which", "can", "is", "could", "do", "does"}
)
_QUESTION_WORDS: Final[frozenset[str]] = _QUESTION_WORDS_NL | _QUESTION_WORDS_EN

# Tiny stoplists for cheap language detection. Not robust for short
# queries, but accurate enough for the "lang" tag's intent (rough
# segmentation of Dutch vs English support traffic in dashboards).
_STOPS_NL: Final[frozenset[str]] = frozenset(
    {"de", "het", "een", "en", "van", "in", "op", "is", "ik", "je", "we", "voor", "naar"}
)
_STOPS_EN: Final[frozenset[str]] = frozenset(
    {"the", "a", "an", "and", "of", "in", "on", "is", "i", "we", "for", "to", "you"}
)

_URL_RE: Final[re.Pattern[str]] = re.compile(r"https?://", re.IGNORECASE)
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\b\w+\b", re.UNICODE)


def _detect_lang(tokens_lower: list[str]) -> str:
    """Heuristic: count stopword overlap; majority wins, default 'other'."""
    if not tokens_lower:
        return "other"
    nl_hits = sum(1 for t in tokens_lower if t in _STOPS_NL)
    en_hits = sum(1 for t in tokens_lower if t in _STOPS_EN)
    if nl_hits == 0 and en_hits == 0:
        return "other"
    return "nl" if nl_hits >= en_hits else "en"


def extract_features(query: str) -> dict:
    """Build the symbolic-feature dict for a shadow_store row.

    Always returns a dict with all keys present (even if values are 0/False)
    so downstream cluster queries can rely on a stable schema.

    Empty or whitespace-only queries return a sentinel-shaped dict with
    everything zeroed; the row is still inserted so the per-org count
    reflects all retrieve calls.
    """
    if not query:
        return {
            "tokens": 0,
            "lang": "other",
            "has_brand": False,
            "brand_count": 0,
            "question_word": False,
            "has_url": False,
            "has_email_pattern": False,
        }

    raw_tokens = _TOKEN_RE.findall(query)
    tokens_lower = [t.lower() for t in raw_tokens]

    brand_hits = [t for t in tokens_lower if t in _BRAND_KEYWORDS]
    first_word = tokens_lower[0] if tokens_lower else ""

    return {
        "tokens": len(raw_tokens),
        "lang": _detect_lang(tokens_lower),
        "has_brand": len(brand_hits) > 0,
        "brand_count": len(brand_hits),
        "question_word": first_word in _QUESTION_WORDS,
        "has_url": _URL_RE.search(query) is not None,
        "has_email_pattern": _EMAIL_RE.search(query) is not None,
    }
