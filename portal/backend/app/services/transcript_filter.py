"""
Noise filter for Vexa transcript segments.

Removes subtitle watermarks, short speakerless segments, consecutive duplicates,
and punctuation-only segments before storage.
"""

import re

_SUBTITLE_PATTERNS = [
    re.compile(r"Sous-titrage", re.IGNORECASE),
    re.compile(r"ST'\s*\d+"),
]

_PUNCTUATION_ONLY = re.compile(r"^[\s.,!?;:\u2026\-\u2013\u2014]*$")


def filter_segments(segments: list[dict]) -> list[dict]:
    """Remove noise segments from a Vexa transcript segment list.

    Filters applied in order (cheapest first):
    1. Empty / whitespace / punctuation-only text
    2. Known subtitle watermark patterns (Sous-titrage, ST' N)
    3. Short speakerless segments (no speaker + duration < 2s)
    4. Consecutive duplicates (same text within 5s)

    Returns a new list; input is not mutated.
    """
    result: list[dict] = []
    for seg in segments:
        text = seg.get("text", "") or ""

        # 1. Empty / punctuation-only
        if not text.strip() or _PUNCTUATION_ONLY.match(text.strip()):
            continue

        # 2. Subtitle watermarks
        if any(p.search(text) for p in _SUBTITLE_PATTERNS):
            continue

        # 3. Short speakerless segments
        speaker = seg.get("speaker") or ""
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        duration = end - start
        if not speaker and duration < 2.0:
            continue

        # 4. Consecutive duplicate (same text within 5s of previous segment)
        if result:
            prev = result[-1]
            prev_end = prev.get("end", 0.0)
            if prev.get("text", "") == text and (start - prev_end) < 5.0:
                continue

        result.append(seg)
    return result
