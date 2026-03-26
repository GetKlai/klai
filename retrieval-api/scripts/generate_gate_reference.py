#!/usr/bin/env python3
"""Generate gate reference queries and save to data/gate_reference.jsonl.

Calls klai-fast to generate 200 queries:
- 100 category A (no retrieval needed): math, logic, general knowledge, grammar
- 100 category B (retrieval needed): domain lookups, policy questions

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


def _prompt_for_category(category: str, count: int) -> str:
    if category == "A":
        return (
            f"Generate exactly {count} short user queries that do NOT require "
            f"knowledge base retrieval. These should be general knowledge, math, "
            f"logic, grammar, or casual conversation queries. "
            f"Generate 50% in Dutch and 50% in English. "
            f"Return ONLY a JSON array of strings, no other text."
        )
    else:
        return (
            f"Generate exactly {count} short user queries that WOULD require "
            f"looking up information in an organization's knowledge base. "
            f"These should be domain-specific: company policies, product details, "
            f"internal procedures, customer info lookups, etc. "
            f"Generate 50% in Dutch and 50% in English. "
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


def main() -> None:
    if OUTPUT_FILE.exists() and OUTPUT_FILE.stat().st_size > 0:
        print(f"Gate reference file already exists at {OUTPUT_FILE}, skipping generation.")
        sys.exit(0)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    print("Generating category A queries (no retrieval needed)...")
    cat_a = _call_llm(_prompt_for_category("A", 100))
    print(f"  Got {len(cat_a)} queries")

    print("Generating category B queries (retrieval needed)...")
    cat_b = _call_llm(_prompt_for_category("B", 100))
    print(f"  Got {len(cat_b)} queries")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        for query in cat_a:
            fh.write(json.dumps({"query": query, "label": "A"}, ensure_ascii=False) + "\n")
        for query in cat_b:
            fh.write(json.dumps({"query": query, "label": "B"}, ensure_ascii=False) + "\n")

    print(f"Wrote {len(cat_a) + len(cat_b)} entries to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
