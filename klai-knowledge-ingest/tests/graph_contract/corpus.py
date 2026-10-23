"""Adversarial entity names for the FalkorDB contract tests.

synthetic-data: generator=klai-knowledge-ingest/tests/graph_contract/corpus.py seed=20261023

Every value is invented here or derived from graphiti's own STOPWORDS list; no
production text is involved. Only the filler of the long name comes from the
seeded generator.
"""

from __future__ import annotations

import random

from graphiti_core.driver.falkordb import STOPWORDS

SEED = 20261023


def _alternating(word: str) -> str:
    return "".join(ch.upper() if i % 2 == 0 else ch.lower() for i, ch in enumerate(word))


def _long_name(rng: random.Random) -> str:
    syllables = ["ka", "lo", "mi", "ne", "ru", "sa", "to", "vi", "xe", "zu"]
    return " ".join("".join(rng.choices(syllables, k=3)) for _ in range(400))


def build_corpus() -> dict[str, list[str]]:
    """Category -> entity names, deterministic for SEED."""
    rng = random.Random(SEED)  # noqa: S311 - deterministic fixture data, not security
    return {
        "stopwords_lower": [word.lower() for word in STOPWORDS],
        "stopwords_upper": [word.upper() for word in STOPWORDS],
        "stopwords_title": [word.title() for word in STOPWORDS],
        "stopwords_alternating": [_alternating(word) for word in STOPWORDS],
        "stopwords_padded": [f" {word} " for word in STOPWORDS[:8]]
        + [f"\t{word.upper()}\n" for word in STOPWORDS[8:16]],
        "whitespace": ["  Lead Space", "Trail Space  ", "\tTabbed\t", "Inner   Gap", "\nNewline\n"],
        "punctuation_only": ["...", "---", "***", "!?", "()", "{}", "[]", "~~", "::", "/"],
        "unicode": [
            "Café Zoë",
            "東京タワー",
            "データベース",
            "\U0001f680 Rocket",
            "Ünïcödé",
            "مرحبا",
            "naïve façade",
        ],
        "long_name": [_long_name(rng), "Short Anchor"],
        "special_characters": [
            "O'Brien",
            'say "hello"',
            "back\\slash",
            "left|right",
            "@mention",
            "dash-ed - name",
            "star*",
            "{curly} braces",
            "(paren) [bracket]",
            "~tilde ^caret",
            "$dollar %percent",
            "colon:name; semi",
            "slash/name",
            "') RETURN 1 //",
            "name\\' OR 1=1",
            "@group_id:other",
        ],
        "empty_after_strip": [" ", "\n\t", "\N{NO-BREAK SPACE}", "", "Real Neighbour"],
    }


def normalise(name: str) -> str:
    """Graphiti's exact-name key: lower-cased, whitespace collapsed."""
    return " ".join(name.lower().split())
