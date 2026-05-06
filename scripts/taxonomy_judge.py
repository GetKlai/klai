"""LLM-as-judge for taxonomy bootstrap proposals.

SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-3: reproducible quality scoring.

Reads a JSON file with proposals, POSTs to LiteLLM, returns structured JSON
with per-proposal scores and an overall_score average.

Usage (inside knowledge-ingest container):
    python /tmp/taxonomy_judge.py /tmp/proposals.json

Input JSON shape (list of proposals):
    [
      {"name": "Telefonie integraties",
       "description": "...",
       "sample_titles": ["url1", "url2", ...]},
      ...
    ]

Output JSON shape (printed to stdout):
    {"scores": [{"name": "...",
                 "coherence": 4,
                 "clarity": 5,
                 "distinctness": 4,
                 "rationale": "..."}, ...],
     "overall_score": 4.13,
     "summary": "...",
     "model": "klai-fast",
     "temperature": 0.1}

Reproducibility: temperature=0.1 + fixed model. Run script 3 times to verify
within-0.5-points stability per AC-3.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

LITELLM_URL = os.environ["LITELLM_URL"]
LITELLM_API_KEY = os.environ["LITELLM_API_KEY"]
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "klai-fast")

JUDGE_SYSTEM_PROMPT = """You are a taxonomy quality auditor for a knowledge-base \
categorization system. You will be given a list of category proposals, each \
with a name, optional description, and 5 sample document titles/URLs.

Score each category on three dimensions on a 1-5 scale:
- coherence: do the sample documents fit the category name? \
(5 = perfect fit, 1 = unrelated)
- clarity: is the category name specific and non-generic? \
(5 = clear and domain-specific, 1 = vague catchall)
- distinctness: does this category clearly differ from the other proposals? \
(5 = distinct concept, 1 = heavy overlap)

Also identify any pairs that overlap heavily (mention them in summary).

Reply ONLY with valid JSON, no markdown, no commentary:
{"scores": [{"name": "<name>",
             "coherence": <int 1-5>,
             "clarity": <int 1-5>,
             "distinctness": <int 1-5>,
             "rationale": "<one short sentence>"}, ...],
 "overall_score": <float, mean of all dimension scores across all proposals>,
 "summary": "<2-3 sentences on overall taxonomy quality and notable issues>"}"""


def build_user_message(proposals: list[dict[str, Any]]) -> str:
    """Render proposals as the LLM's user-message body."""
    lines = [f"You have {len(proposals)} category proposals to score:\n"]
    for i, p in enumerate(proposals, 1):
        name = p.get("name") or p.get("title") or "<unnamed>"
        desc = p.get("description") or ""
        titles = p.get("sample_titles") or []
        lines.append(f"\nProposal {i}: {name}")
        if desc:
            lines.append(f"  Description: {desc}")
        else:
            lines.append("  Description: (empty)")
        lines.append("  Sample documents:")
        for t in titles[:5]:
            lines.append(f"    - {t}")
    return "\n".join(lines)


def judge(proposals: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the judge once. Deterministic-ish (temperature=0.1)."""
    if not proposals:
        return {
            "scores": [],
            "overall_score": 0.0,
            "summary": "No proposals to judge.",
            "model": JUDGE_MODEL,
            "temperature": 0.1,
        }

    user_message = build_user_message(proposals)

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{LITELLM_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {LITELLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": JUDGE_MODEL,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.1,
                "max_tokens": 2000,
                "seed": 42,
            },
        )
        resp.raise_for_status()
        content = (resp.json()["choices"][0]["message"]["content"] or "").strip()

    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    parsed = json.loads(content)
    parsed["model"] = JUDGE_MODEL
    parsed["temperature"] = 0.1
    return parsed


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: taxonomy_judge.py <proposals.json>", file=sys.stderr)
        sys.exit(2)

    with open(sys.argv[1], encoding="utf-8") as f:
        proposals = json.load(f)

    result = judge(proposals)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
