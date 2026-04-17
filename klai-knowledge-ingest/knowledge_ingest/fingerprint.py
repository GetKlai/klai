"""Content fingerprinting for auth-guard canary page detection.

SPEC-CRAWL-004 REQ-3 + REQ-6: compute a 64-bit SimHash from crawl preview content
so the preview URL can serve as a canary page.

This is a stdlib-only reimplementation of the SimHash algorithm used in
``klai-connector/app/services/content_fingerprint.py`` (which uses
``trafilatura.deduplication.Simhash``). Both produce identical output for the
same input — verified by the compatibility test in the connector test suite.

Algorithm (Charikar SimHash):
1. Tokenize: split on whitespace, strip punctuation, keep alphanumeric tokens.
2. Filter: require tokens with length > threshold such that ≥ 32 tokens remain.
3. Hash: ``blake2b(token, digest_size=8)`` → 64-bit integer per token.
4. Aggregate: per-bit weighted sum (+1 if bit set, -1 if not) across all tokens.
5. Collapse: bit i = 1 if sum[i] >= 0, else 0. Produces a 64-bit fingerprint.

No I/O, no logging, no external dependencies.
"""

from __future__ import annotations

import re
import string
from hashlib import blake2b

_MIN_WORDS = 20
_SIMHASH_BITS = 64


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax for cleaner tokenization."""
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[#*_`\[\]()!]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sample_tokens(text: str) -> list[str]:
    """Split text into filtered alphanumeric tokens (mirrors trafilatura.sample_tokens)."""
    tokens = []
    for token in text.split():
        token = token.strip(string.punctuation)
        if token.isalnum():
            tokens.append(token)
    # Progressively lower min-length threshold to get ≥ 32 tokens
    for min_len in range(4, -1, -1):
        sample = [t for t in tokens if len(t) > min_len]
        if len(sample) >= _SIMHASH_BITS // 2:
            return sample
    return [t for t in tokens if len(t) > 0]


def _token_hash(token: str) -> int:
    """64-bit hash of a single token via blake2b (matches trafilatura._hash)."""
    return int.from_bytes(
        blake2b(token.encode(), digest_size=8).digest(), "big"
    )


def compute_content_fingerprint(markdown: str) -> str:
    """Return 16-char hex SimHash of markdown content, or '' if too short.

    Produces identical output to ``klai-connector``'s version for the same input.

    Args:
        markdown: Raw markdown string from the crawl preview.

    Returns:
        Zero-padded 16-character hex string (64-bit SimHash), or empty string
        if the stripped text contains fewer than 20 words.
    """
    if not markdown:
        return ""

    stripped = _strip_markdown(markdown)
    words = stripped.split()

    if len(words) < _MIN_WORDS:
        return ""

    tokens = _sample_tokens(stripped)
    if not tokens:
        return ""

    vector = [0] * _SIMHASH_BITS
    for token in tokens:
        h = _token_hash(token)
        for i in range(_SIMHASH_BITS):
            vector[i] += 1 if h & (1 << i) else -1

    fingerprint = sum(1 << i for i in range(_SIMHASH_BITS) if vector[i] >= 0)
    return f"{fingerprint:016x}"
