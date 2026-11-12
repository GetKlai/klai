"""Cross-lingual eval runner for SPEC-RAG-MULTILINGUAL-CHAT-001.

Loads ``test_queries_cross_lingual.json``, asks each query through the
portal's partner chat endpoint (the chat path customers use), and reports
per-language correctness. Run manually before/after prompt changes to
verify the ``language_correctness >= 95%`` gate per REQ-05.

Usage:
    # Against a running portal-api (port 8010 by default); the partner key
    # decides the organisation and knowledge bases that answer.
    KLAI_PARTNER_API_KEY=pk_... python evaluation/cross_lingual_runner.py

    # Custom URL + output path
    python evaluation/cross_lingual_runner.py \
        --portal-url http://localhost:8010 \
        --output evaluation/results/cross_lingual.json

The runner intentionally does NOT use RAGAS or any LLM-as-judge — it
only checks one thing per query: did the response come back in the
language of the query? That is what REQ-05 gates on.

Citation correctness, faithfulness, and answer-relevance are covered by
the knowledge-ingest RAGAS suites; they're not re-implemented here. After
a Phase-2 merge, the operator runs this script and the relevant RAGAS suite
before declaring the language gate satisfied.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

# Ensure retrieval_api is importable when running as a script
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from klai_chat_prompts.language import (  # noqa: E402
    UNKNOWN_LANGUAGE,
    identify_surface_language,
    language_correctness,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_QUERIES_FILE = EVAL_DIR / "test_queries_cross_lingual.json"
DEFAULT_OUTPUT_FILE = EVAL_DIR / "results" / "cross_lingual.json"
DEFAULT_PORTAL_URL = "http://localhost:8010"


# Strict gate per REQ-05.
LANGUAGE_CORRECTNESS_FLOOR: float = 0.95


SynthFn = Callable[[str, str], Awaitable[str]]


def _load_queries(path: Path) -> list[dict[str, Any]]:
    """Load the cross-lingual test queries from JSON.

    Each entry MUST have ``query``, ``language``, ``intent_id``,
    ``org_id``, ``ground_truth_chunks``, ``expected_answer``.
    """
    if not path.exists():
        raise FileNotFoundError(f"Cross-lingual test queries not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"Expected non-empty list at {path}, got {type(data)!r}")
    required_keys = {"query", "language", "intent_id"}
    for i, q in enumerate(data):
        missing = required_keys - q.keys()
        if missing:
            raise ValueError(f"Query {i} missing keys: {missing}")
    return data


async def _http_synthesize(
    query: str,
    *,
    portal_url: str,
    api_key: str,
    timeout_s: float = 60.0,
) -> str:
    """Ask ``query`` through portal-api's partner chat endpoint and return the answer text.

    This is the chat path customers use (retrieval, prompt and answer in
    portal-api), so the language check measures what they see. The partner
    key binds the organisation, so a query's ``org_id`` is not sent.
    """
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(
            f"{portal_url}/partner/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            # A request without knowledge fields takes the general model route
            # for a key that allows it, which answers without retrieval.
            json={
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "knowledge": {"enabled": True},
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def score_query(
    query_entry: dict[str, Any],
    synth_fn: SynthFn,
) -> dict[str, Any]:
    """Score a single cross-lingual query against the synthesis service.

    Returns a result dict with ``query``, ``expected_language``,
    ``response_text``, ``detected_response_language``,
    ``language_correctness`` (bool | None), and ``error`` (str | None).
    """
    query = query_entry["query"]
    expected_language = query_entry["language"]
    org_id = query_entry["org_id"]

    try:
        response = await synth_fn(query, org_id)
    except Exception as exc:
        logger.warning("score_query_synth_failed: %s — %s", query[:60], exc)
        return {
            "query": query,
            "expected_language": expected_language,
            "intent_id": query_entry["intent_id"],
            "response_text": None,
            "detected_response_language": None,
            "language_correctness": None,
            "error": str(exc),
        }

    detected = identify_surface_language(response or "") or UNKNOWN_LANGUAGE
    correct = language_correctness(expected_language, detected)
    return {
        "query": query,
        "expected_language": expected_language,
        "intent_id": query_entry["intent_id"],
        "response_text": response,
        "detected_response_language": detected,
        "language_correctness": correct,
        "error": None,
    }


def aggregate_results(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-query scores into per-language and overall metrics.

    Excludes None values (UNKNOWN_LANGUAGE on either side) from the
    correctness ratio — those are skipped, not counted as failures.
    """
    by_lang_total: Counter[str] = Counter()
    by_lang_correct: Counter[str] = Counter()
    by_lang_skipped: Counter[str] = Counter()
    by_lang_errors: Counter[str] = Counter()

    for r in scored:
        lang = r["expected_language"]
        if r["error"] is not None:
            by_lang_errors[lang] += 1
            continue
        if r["language_correctness"] is None:
            by_lang_skipped[lang] += 1
            continue
        by_lang_total[lang] += 1
        if r["language_correctness"]:
            by_lang_correct[lang] += 1

    per_language: dict[str, dict[str, Any]] = {}
    for lang in sorted(set(by_lang_total) | set(by_lang_skipped) | set(by_lang_errors)):
        total = by_lang_total[lang]
        correct = by_lang_correct[lang]
        rate = (correct / total) if total else None
        per_language[lang] = {
            "total_scored": total,
            "correct": correct,
            "skipped_unknown": by_lang_skipped[lang],
            "errors": by_lang_errors[lang],
            "language_correctness_rate": rate,
            "passes_floor": (rate is not None and rate >= LANGUAGE_CORRECTNESS_FLOOR),
        }

    overall_total = sum(by_lang_total.values())
    overall_correct = sum(by_lang_correct.values())
    overall_rate = (overall_correct / overall_total) if overall_total else None

    failing_languages = [lang for lang, v in per_language.items() if not v["passes_floor"]]

    return {
        "per_language": per_language,
        "overall": {
            "total_scored": overall_total,
            "correct": overall_correct,
            "language_correctness_rate": overall_rate,
        },
        "floor": LANGUAGE_CORRECTNESS_FLOOR,
        "gate_passes": len(failing_languages) == 0,
        "failing_languages": failing_languages,
    }


async def run(
    queries_file: Path,
    output_file: Path,
    synth_fn: SynthFn,
    *,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Run the full cross-lingual eval and write the report to disk."""
    queries = _load_queries(queries_file)
    logger.info("Loaded %d cross-lingual queries", len(queries))

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(q: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await score_query(q, synth_fn)

    scored = await asyncio.gather(*[_bounded(q) for q in queries])
    aggregate = aggregate_results(scored)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(
            {"summary": aggregate, "results": scored},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote cross-lingual eval report to %s", output_file)

    # Pretty summary print
    print("=" * 60)
    print("Cross-Lingual Eval Summary")
    print("=" * 60)
    for lang, v in aggregate["per_language"].items():
        rate = v["language_correctness_rate"]
        rate_str = f"{rate:.1%}" if rate is not None else "n/a"
        marker = "PASS" if v["passes_floor"] else "FAIL"
        print(
            f"  [{marker}] {lang}: {v['correct']}/{v['total_scored']} = {rate_str} "
            f"(skipped: {v['skipped_unknown']}, errors: {v['errors']})"
        )
    overall = aggregate["overall"]
    overall_rate_str = (
        f"{overall['language_correctness_rate']:.1%}"
        if overall["language_correctness_rate"] is not None
        else "n/a"
    )
    print(f"  Overall: {overall['correct']}/{overall['total_scored']} = {overall_rate_str}")
    print("-" * 60)
    if aggregate["gate_passes"]:
        print(f"GATE PASSES — all languages >= {LANGUAGE_CORRECTNESS_FLOOR:.0%}")
    else:
        print(f"GATE FAILS — failing languages: {', '.join(aggregate['failing_languages'])}")
    print("=" * 60)

    return aggregate


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-lingual eval for SPEC-RAG-MULTILINGUAL-CHAT-001"
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_QUERIES_FILE,
        help="Path to cross-lingual test queries JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Path to write the report JSON",
    )
    parser.add_argument(
        "--portal-url",
        default=DEFAULT_PORTAL_URL,
        help="Base URL of the running portal-api",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max concurrent synthesis calls",
    )
    return parser.parse_args()


def main() -> int:
    import os

    args = _parse_args()
    # A partner key of the organisation whose knowledge bases answer; read from
    # the environment so it stays out of shell history.
    api_key = os.environ["KLAI_PARTNER_API_KEY"]

    async def _http_synth(query: str, org_id: str) -> str:
        return await _http_synthesize(query, portal_url=args.portal_url, api_key=api_key)

    aggregate = asyncio.run(
        run(args.queries, args.output, _http_synth, concurrency=args.concurrency)
    )
    return 0 if aggregate["gate_passes"] else 1


if __name__ == "__main__":
    sys.exit(main())
