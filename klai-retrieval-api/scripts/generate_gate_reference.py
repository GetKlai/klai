#!/usr/bin/env python3
"""Generate gate reference queries and save to data/gate_reference.jsonl.

Calls klai-fast to generate queries across the six target languages
defined by SPEC-RAG-MULTILINGUAL-CHAT-001 (NL, EN, DE, FR, PT, ES):
- 100 category A (no retrieval needed): math, logic, general knowledge, grammar
- 100 category B (retrieval needed): domain lookups, policy questions

Each category is generated as evenly distributed across the six target
languages instead of the previous 50/50 NL/EN split. Output schema gains
a "language" field so downstream consumers can filter / aggregate per
language without re-detecting.

Idempotent: skips generation if the file already exists and has content.

Usage:
    python scripts/generate_gate_reference.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

OUTPUT_FILE = Path(__file__).parent.parent / "retrieval_api" / "data" / "gate_reference.jsonl"

LITELLM_URL = os.environ.get("LITELLM_URL", "http://litellm:4000")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")


# SPEC-RAG-MULTILINGUAL-CHAT-001: target language list. Affirms NL +
# EN + DE + FR + PT + ES. Afrikaans is intentionally NOT included
# (ZA team works in English).
TARGET_LANGUAGES: dict[str, str] = {
    "nl": "Dutch",
    "en": "English",
    "de": "German",
    "fr": "French",
    "pt": "Portuguese",
    "es": "Spanish",
}


def _prompt_for_category_language(category: str, language_name: str, count: int) -> str:
    """Build the LLM prompt for a single (category, language) cell."""
    if category == "A":
        return (
            f"Generate exactly {count} short user queries written in {language_name} "
            f"that do NOT require knowledge base retrieval. These should be general "
            f"knowledge, math, logic, grammar, or casual conversation queries. "
            f"Return ONLY a JSON array of strings, no other text."
        )
    else:
        return (
            f"Generate exactly {count} short user queries written in {language_name} "
            f"that WOULD require looking up information in an organization's "
            f"knowledge base. These should be domain-specific: company policies, "
            f"product details, internal procedures, customer info lookups, etc. "
            f"Return ONLY a JSON array of strings, no other text."
        )


def _call_llm(prompt: str) -> list[str]:
    headers = {}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"

    body = {
        "model": "klai-fast",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.8,
    }

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{LITELLM_URL}/v1/chat/completions",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

    # Parse JSON array from response
    # Handle potential markdown code blocks
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])

    return json.loads(content)


def _per_language_count(total: int) -> dict[str, int]:
    """Split ``total`` queries roughly evenly across target languages.

    With 6 languages and total=100, this yields {17, 17, 17, 17, 16, 16}
    so the sum equals 100. The remainder is spread over the first
    languages in the dict for determinism.
    """
    n = len(TARGET_LANGUAGES)
    base, extra = divmod(total, n)
    counts: dict[str, int] = {}
    for i, code in enumerate(TARGET_LANGUAGES):
        counts[code] = base + (1 if i < extra else 0)
    return counts


def _generate_category(category: str, total: int) -> list[dict]:
    """Generate ``total`` entries for ``category``, evenly across languages."""
    per_lang = _per_language_count(total)
    entries: list[dict] = []
    for lang_code, language_name in TARGET_LANGUAGES.items():
        count = per_lang[lang_code]
        if count <= 0:
            continue
        prompt = _prompt_for_category_language(category, language_name, count)
        print(f"  Generating {count} {language_name} ({lang_code}) cat-{category} queries...")
        try:
            queries = _call_llm(prompt)
        except Exception as exc:
            print(f"    WARN: {language_name} cat-{category} generation failed: {exc}")
            continue
        for q in queries:
            entries.append({"query": q, "label": category, "language": lang_code})
    return entries


def main() -> None:
    if OUTPUT_FILE.exists() and OUTPUT_FILE.stat().st_size > 0:
        print(f"Gate reference file already exists at {OUTPUT_FILE}, skipping generation.")
        sys.exit(0)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Generating category A queries (no retrieval needed)...")
    cat_a = _generate_category("A", 100)
    print(f"  Got {len(cat_a)} queries across {len(TARGET_LANGUAGES)} languages")

    print("Generating category B queries (retrieval needed)...")
    cat_b = _generate_category("B", 100)
    print(f"  Got {len(cat_b)} queries across {len(TARGET_LANGUAGES)} languages")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        for entry in cat_a + cat_b:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Wrote {len(cat_a) + len(cat_b)} entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
