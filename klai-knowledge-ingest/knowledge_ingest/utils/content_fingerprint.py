"""SimHash content fingerprinting for near-duplicate template detection.

SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-01.

Computes a 64-bit SimHash of normalised page text. Walls are pages whose
normalised content is near-identical (Hamming distance <= 3) to many other
pages in the same KB; the fingerprint enables that cluster lookup at SQL
speed.

Algorithm:

1. Normalise: replace markdown anchors ``[text](url)`` with their bare anchor
   text, replace remaining bare URLs with the literal token ``<URL>``,
   lowercase, collapse whitespace.
2. Tokenise on word boundaries (``\\b\\w+\\b``).
3. For each unique token, take a stable 64-bit hash (blake2b stdlib, NOT
   Python's randomised ``hash()``). Weight by token frequency.
4. Build a 64-element vector: for every bit position, add the token weight if
   the token's hash has that bit set, else subtract it.
5. The fingerprint's bit at position ``b`` is 1 iff vector[b] > 0, else 0.
6. Reinterpret the resulting unsigned 64-bit value as a signed int64 so it
   round-trips through PostgreSQL ``bigint`` unchanged.

The function is pure: no I/O, no logging, no global state. Identical inputs
always produce identical outputs across processes (blake2b is deterministic;
Python's hash randomisation does NOT affect us).

Why blake2b? It is in the stdlib (no new dependency), is faster than sha1 on
short inputs, and yields exactly 64 bits when we ask for ``digest_size=8``.
xxhash would be marginally faster but adds a dependency for negligible gain.

Why not the ``simhash`` PyPI package? It pins MurmurHash and Python 2 idioms
and is unmaintained. ~50 LOC in-tree is simpler to audit and lock down.
"""

from __future__ import annotations

import hashlib
import re

import numpy as np

__all__ = [
    "compute_simhash",
    "hamming_distance",
]

# Pre-computed 64 bit-masks for vectorised "is bit b set" checks. uint64 lets
# numpy do the bitwise AND in a single C-level kernel instead of looping in
# Python. Reused across calls to avoid reallocation.
_BIT_MASKS_U64 = (np.uint64(1) << np.arange(64, dtype=np.uint64)).reshape(1, 64)

# ---------------------------------------------------------------------------
# Pre-hash normalisation
# ---------------------------------------------------------------------------

# Match a markdown anchor ``[text](url)`` capturing the visible text only. The
# anchor URL is dropped so per-page link-target variation does not leak into
# the hash. Multiline-friendly: anchors do not span newlines so default flags
# are fine.
_MD_ANCHOR_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")

# Match a bare URL (http or https) up to the first whitespace, closing paren,
# or closing bracket. Replaced with the placeholder token ``<URL>``.
_URL_RE = re.compile(r"https?://[^\s)\]]+")

_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"\b\w+\b", flags=re.UNICODE)


def _normalise(text: str) -> str:
    """Apply normalisation steps in REQ-01 order.

    Order matters: anchors must be reduced to text BEFORE URL stripping, or
    the bare URL inside the anchor would survive. Lowercase last so anchor
    text capitalisation does not affect ordering of the previous steps.
    """
    text = _MD_ANCHOR_RE.sub(lambda m: m.group(1), text)
    text = _URL_RE.sub("<URL>", text)
    text = text.lower()
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _tokenise(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------


def _hash64(token: str) -> int:
    """Stable 64-bit unsigned hash of a token via blake2b stdlib."""
    return int.from_bytes(
        hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(),
        "big",
    )


_INT64_SIGN_BIT = 1 << 63
_INT64_MOD = 1 << 64
_UINT64_MASK = _INT64_MOD - 1


def _to_signed_int64(unsigned: int) -> int:
    """Reinterpret a 64-bit unsigned value as signed int64 (PostgreSQL bigint)."""
    if unsigned & _INT64_SIGN_BIT:
        return unsigned - _INT64_MOD
    return unsigned


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_simhash(text: str) -> int:
    """Return a 64-bit SimHash of ``text``, packed as signed int64.

    Empty input returns 0. The function is deterministic across runs and
    Python interpreters: blake2b is content-only and Python's hash
    randomisation does not affect us.
    """
    if not text:
        return 0

    normalised = _normalise(text)
    tokens = _tokenise(normalised)
    if not tokens:
        return 0

    # Token frequency as the weight vector. Sum equal hashes only once; doing
    # the lookup inside the bit loop is materially faster than re-hashing.
    counts: dict[str, int] = {}
    for tok in tokens:
        counts[tok] = counts.get(tok, 0) + 1

    # Vectorised bit accumulation. The pure-Python equivalent is a 64-element
    # accumulator updated per token; on 100 KB markdown that costs ~2.5 ms in
    # CPython 3.14 and breaches REQ-08 (p99 < 5 ms). numpy handles the matrix
    # op in a single C kernel.
    n = len(counts)
    hashes = np.empty(n, dtype=np.uint64)
    weights = np.empty(n, dtype=np.int64)
    for i, (tok, weight) in enumerate(counts.items()):
        hashes[i] = _hash64(tok)
        weights[i] = weight

    # bit_set[i, b] is True iff bit b of hashes[i] is set.
    bit_set = (hashes.reshape(-1, 1) & _BIT_MASKS_U64) != np.uint64(0)
    # Map True/False to +1/-1 then weight per row; column-sum to get per-bit
    # signed total. Final bit is set iff column sum > 0.
    signs = np.where(bit_set, 1, -1).astype(np.int64)
    bit_sums = (signs * weights.reshape(-1, 1)).sum(axis=0)

    fingerprint = 0
    for bit in range(64):
        if bit_sums[bit] > 0:
            fingerprint |= 1 << bit

    return _to_signed_int64(fingerprint)


def hamming_distance(a: int, b: int) -> int:
    """Count differing bits between two 64-bit fingerprints.

    Inputs may be signed (post round-trip from PostgreSQL bigint) or unsigned;
    masking to 64 bits before XOR avoids Python's arbitrary-precision negative
    bit semantics.
    """
    return ((a & _UINT64_MASK) ^ (b & _UINT64_MASK)).bit_count()
