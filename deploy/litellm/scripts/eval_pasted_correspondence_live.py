"""Live end-to-end eval for pasted-correspondence distillation
(SPEC-RAG-CORRESPONDENCE-DISTILL-001 REQ-6, proving AC-2 with repeated samples).

Exercises production components for every ``mix: pasted_correspondence`` canary
in the shared knowledge-ingest eval suite, ``--samples`` times each
(default 3, since Mistral is non-deterministic even at temperature=0.0 across
API-level retries/routing):

  1. Compute ``pasted_correspondence`` via the SAME detector production uses
     (``klai_pasted_correspondence.latest_user_turn_has_correspondence``) —
     NOT hardcoded, so the suite's negative-class control (a plain question,
     no pasted-correspondence shape) exercises the plain prompt/path exactly
     like production would, proving AC-6's "negative-class control shows zero
     prompt diff vs. baseline" claim empirically rather than by assumption.
  2. Call ``rewrite_and_classify(...)`` (not the lower-level ``rewrite_query``)
     with an empty taxonomy tree, mirroring production's actual call site in
     ``klai_knowledge.py`` — this also means the module's own internal
     short-circuit (empty taxonomy -> delegates to ``rewrite_query``) is
     exercised for real rather than bypassed.
  3. Retrieve with ``coreference_resolved=rewrite_decided(meta)``, matching
     ``klai_knowledge.py``'s own retrieve-body construction.
  4. Match expected_chunks markers ONLY within the top-5 results — AC-2's
     literal bar ("present in top-5"), not "anywhere in top-10".
  5. Ask the production answer model with a prompt assembled from the canonical
     branch, correspondence, context, and language helpers, then verify the raw
     answer with the same deterministic contract inspector as the hook.

This is deliberately a component-level live eval: it fixes the retrieved
evidence for repeatable canaries and therefore does not call
``KlaiKnowledgeHook.async_pre_call_hook`` end to end. Hook wiring remains covered
by the LiteLLM integration tests.

Pacing (Sol delta-review Fix 4/5, 2026-08-18): this script is a SEPARATE
process from the production litellm hook, so it gets its own tiny, dedicated
slice of the direct-Mistral rate budget (default 0.05 rps / burst 1) rather
than sharing the hook's process-local token bucket — two independent
process-local buckets on the SAME upstream Mistral cap would otherwise risk
exceeding it together. Samples are paced client-side (one sample every
~20s at the default slice, before every sample except the very first in the
whole run — not just per-canary) so the shared bucket always has a token by
call time and the 1.5s-bounded rewrite-call acquire() never times out. Each
sample's answer call then waits for the same limiter's next token. At the
default slice, 3 canaries x 3 samples = 9 samples takes roughly 6 minutes.

A sample whose rewrite fell back to the raw query (limiter timeout, guard
rejection, or any other ``meta["skipped"]`` reason) is not a real
distillation attempt and is excluded from the canary's pass rate — see
``_run_one_sample`` / ``_run_canary`` below.

Scope note: this proves AC-2 (top-5 presence + repeated-sample pass rate).
It does NOT compute AC-6's ``context_precision`` metric — that requires the
full RAGAS judge pipeline in klai-knowledge-ingest's nightly eval harness
(``knowledge_ingest/eval/ragas_runner.py``). Closing AC-6 fully still means
running the actual ``chat.yaml`` suite through that harness.

THIS SCRIPT CANNOT RUN IN STANDARD CI. It needs:
  - Real Mistral quota (goes through the SAME shared direct_mistral_limiter()
    token bucket as production, so it will not cause a 429 storm, but it DOES
    consume real quota — do not loop this in a tight retry).
  - Network access to retrieval-api's Docker-internal hostname
    (KNOWLEDGE_RETRIEVE_URL, e.g. http://retrieval-api:8040/retrieve).
  - RETRIEVAL_INTERNAL_SECRET / PORTAL_INTERNAL_SECRET and MISTRAL_API_KEY set.

This is the same constraint the existing knowledge-ingest RAGAS eval harness
already has — it is designed for manual/server-side invocation, not local-
machine or standard GH Actions CI. Run it explicitly on the server:

    docker exec klai-core-litellm-1 python scripts/eval_pasted_correspondence_live.py

The suite YAML is bind-mounted read-only at ``/app/eval_suites/chat.yaml``
(see deploy/docker-compose.yml, litellm service) — it lives in a different
service's directory (klai-knowledge-ingest) so it is not reachable via a
repo-relative path from inside the container. Override with
``PASTED_CORRESPONDENCE_EVAL_SUITE`` or ``--suite`` for local/manual runs
against a repo checkout.

The core logic this script orchestrates (canary loading, chunk matching,
pass-rate aggregation) is unit-tested with no network calls in
tests/test_correspondence_eval.py, which DOES run in normal CI.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx

# Sol delta-review Fix 4: this script is a SEPARATE process from the
# production litellm hook's own direct_mistral_limiter() token bucket.
# Reserve a tiny, dedicated budget slice for this second process so a live
# eval can never push the combined direct-Mistral traffic over the upstream
# cap alongside the production hook's own bucket (90 router + 6 hook + 3
# eval = 99 < ~100 rpm). setdefault: an operator override still wins. Must
# run BEFORE klai_kb_query_rewrite is imported (below) since it reads these
# env vars into module-level constants at import time.
os.environ.setdefault("DIRECT_MISTRAL_RATE_LIMIT_RPS", "0.05")
os.environ.setdefault("DIRECT_MISTRAL_RATE_LIMIT_BURST", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from klai_answer_epistemics import inspect_answer_epistemics
from klai_citations import evidence_pack_items_as_chunks
from klai_correspondence_eval import (
    CorrespondenceCanary,
    answer_shape_matches_expectation,
    chunk_matches_expected,
    load_pasted_correspondence_canaries,
    summarize_canary_samples,
)
from klai_kb_answer_policy import compose_kb_mode_chat_prefix, kb_zero_chunks_notice
from klai_kb_confidence_policy import (
    LOW_CONFIDENCE_INJECTION_DISABLED,
    LOW_CONFIDENCE_INJECTION_TEXT,
    LOW_CONFIDENCE_OPEN_CONTEXT_TEXT,
    should_apply_low_confidence_injection,
)
from klai_kb_context_prompt import build_kb_context_prompt
from klai_kb_llm_safety import (
    check_llm_safety,
    chunk_safety_text,
    llm_safety_enabled,
    llm_safety_enforces,
)
from klai_kb_portal_client import retrieve, retrieve_headers
from klai_kb_query_rewrite import (
    DIRECT_MISTRAL_RATE_LIMIT_RPS,
    MISTRAL_API_KEY,
    MISTRAL_API_URL,
    QUERY_REWRITE_MODEL,
    direct_mistral_limiter,
    rewrite_and_classify,
    rewrite_decided,
)
from klai_kb_system_prompt import (
    append_final_language_reminder,
    prepend_system_prefix,
)
from klai_llm_safety import SafetyPhase
from klai_pasted_correspondence import (
    PASTED_CORRESPONDENCE_SCOPE,
    latest_user_turn_has_correspondence,
)

_DEFAULT_SUITE = Path(
    os.environ.get("PASTED_CORRESPONDENCE_EVAL_SUITE", "/app/eval_suites/chat.yaml")
)
_RETRIEVE_TOP_K = 10
_MATCH_WINDOW = 5  # AC-2: "present in top-5", not "anywhere in top-10"
_RETRIEVE_TIMEOUT = 15.0
_ANSWER_TIMEOUT = 45.0
KB_IMAGES_BASE_URL = os.getenv("KB_IMAGES_BASE_URL", "https://getklai.getklai.com")


def _detect_pasted_correspondence(query: str) -> bool:
    """Same detector production uses, applied to a synthetic single-turn message list."""
    return latest_user_turn_has_correspondence([{"role": "user", "content": query}])


def _production_safe_answer_chunks(
    canary: CorrespondenceCanary, chunks: list[dict]
) -> list[dict]:
    """Apply the same context-safety filtering as the production hook."""
    if not llm_safety_enabled():
        return chunks

    safe_chunks: list[dict] = []
    blocked = 0
    safety_metadata: dict = {}
    for chunk in chunks:
        decision = check_llm_safety(
            phase=SafetyPhase.CONTEXT,
            text=chunk_safety_text(chunk),
            query=canary.query,
            org_id=canary.org_zitadel_id,
            user_id=None,
            metadata=safety_metadata,
            chunk_id=chunk.get("chunk_id"),
        )
        if decision is None or decision.allowed:
            safe_chunks.append(chunk)
        else:
            blocked += 1

    if blocked and not safe_chunks and llm_safety_enforces():
        raise ValueError("retrieval eval answer context was fully blocked by LLM safety")
    return safe_chunks


class _SamplePacer:
    """Client-side pacing for the shared, tiny per-process token bucket.

    Sol delta-review Fix 4/5: sleeps ``delay_seconds`` before every sample
    except the very first one across the ENTIRE run — not just per-canary,
    since all canaries share the same underlying direct_mistral_limiter()
    bucket. This keeps the bucket topped up by call time so the
    1.5s-bounded rewrite-call acquire() never times out under this script's
    own tight budget.
    """

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self._started = False

    async def wait(self) -> None:
        if self._started:
            await asyncio.sleep(self._delay_seconds)
        self._started = True


def _build_answer_eval_messages(
    query: str,
    chunks: list[dict],
    *,
    pasted_correspondence: bool,
    confidence_band: object = None,
) -> list[dict]:
    """Reproduce the Open chunks-present or zero-chunks message composition."""
    messages: list[dict] = [{"role": "user", "content": query}]
    if pasted_correspondence:
        messages.insert(0, {"role": "system", "content": PASTED_CORRESPONDENCE_SCOPE})
    if chunks:
        low_confidence_inject = should_apply_low_confidence_injection(
            confidence_band,
            user_query=query,
            evidence_chunks=chunks,
        )
        context_prompt = build_kb_context_prompt(
            kb_narrow=False,
            context_chunks=chunks,
            trusted_sources=[],
            templates_block="",
            images_base_url=KB_IMAGES_BASE_URL,
            low_confidence_inject=low_confidence_inject,
            low_confidence_injection_disabled=LOW_CONFIDENCE_INJECTION_DISABLED,
            low_confidence_strict_text=LOW_CONFIDENCE_INJECTION_TEXT,
            low_confidence_open_text=LOW_CONFIDENCE_OPEN_CONTEXT_TEXT,
        )
        context_block = context_prompt.context_block
    else:
        context_block = kb_zero_chunks_notice(False)
    prepend_system_prefix(
        messages,
        compose_kb_mode_chat_prefix(False, context_block),
    )
    append_final_language_reminder(messages, include_kb_reminder=bool(chunks))
    return messages


async def _answer_shape_matches(
    canary: CorrespondenceCanary,
    chunks: list[dict],
    *,
    pasted_correspondence: bool,
    confidence_band: object = None,
) -> bool:
    """Generate and inspect one raw answer using the production prompt contract."""
    payload = {
        "model": QUERY_REWRITE_MODEL,
        "messages": _build_answer_eval_messages(
            canary.query,
            chunks,
            pasted_correspondence=pasted_correspondence,
            confidence_band=confidence_band,
        ),
        "temperature": 0.0,
        "max_tokens": 1200,
    }
    await direct_mistral_limiter().acquire()
    async with httpx.AsyncClient(timeout=_ANSWER_TIMEOUT) as answer_http:
        response = await answer_http.post(
            MISTRAL_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY}",
                "Content-Type": "application/json",
            },
        )
    response.raise_for_status()
    answer = response.json()["choices"][0]["message"]["content"]
    if not isinstance(answer, str):
        raise TypeError("Mistral answer eval returned non-string content")
    inspection = inspect_answer_epistemics(
        answer,
        user_turn=canary.query,
        evidence_chunks=chunks,
        correspondence_detected=pasted_correspondence,
        telemetry_level="shadow",
        latest_turn_correspondence_detected=pasted_correspondence,
    )
    return answer_shape_matches_expectation(canary, inspection, raw_answer=answer)


async def _run_one_sample(
    http: httpx.AsyncClient, canary: CorrespondenceCanary
) -> tuple[bool, bool, str, bool, str | None]:
    """Distill + retrieve once for one canary.

    Returns ``(retrieval_passed, answer_shape_passed, distilled_query,
    pasted_correspondence_detected, skipped_reason)``. ``skipped_reason`` is
    non-None (mirrors ``meta["skipped"]``) when the rewrite fell back to the
    raw query — e.g. a limiter timeout or destructive-rewrite-guard
    rejection. Such a sample never went through real distillation and must
    not count toward the canary's pass rate (Sol delta-review Fix 5).
    """
    pasted_correspondence = _detect_pasted_correspondence(canary.query)
    distilled_query, _classified_node_ids, meta = await rewrite_and_classify(
        canary.query,
        history=[],
        taxonomy_trees=[],
        pasted_correspondence=pasted_correspondence,
    )
    skipped = meta.get("skipped")
    if skipped is not None:
        return False, False, distilled_query, pasted_correspondence, skipped

    body = {
        "query": distilled_query,
        "raw_query": canary.query,
        "coreference_resolved": rewrite_decided(meta),
        "org_id": canary.org_zitadel_id,
        "top_k": _RETRIEVE_TOP_K,
    }
    resp = await retrieve(http, body)
    resp.raise_for_status()
    retrieval_payload = resp.json()
    if not isinstance(retrieval_payload, dict):
        raise TypeError("retrieval eval returned a non-object payload")
    raw_chunks = retrieval_payload.get("chunks")
    raw_chunks = raw_chunks if isinstance(raw_chunks, list) else []
    retrieval_match_chunks = raw_chunks[:_MATCH_WINDOW]
    evidence_pack = retrieval_payload.get("evidence_pack")
    if not isinstance(evidence_pack, dict):
        raise TypeError("retrieval eval response is missing the production evidence_pack")
    answer_chunks = _production_safe_answer_chunks(
        canary,
        evidence_pack_items_as_chunks(evidence_pack),
    )

    all_matched = all(
        any(chunk_matches_expected(expected, chunk) for chunk in retrieval_match_chunks)
        for expected in canary.expected_chunks
    )
    answer_shape_ok = await _answer_shape_matches(
        canary,
        answer_chunks,
        pasted_correspondence=pasted_correspondence,
        confidence_band=retrieval_payload.get("confidence_band"),
    )
    return (
        all_matched,
        answer_shape_ok,
        distilled_query,
        pasted_correspondence,
        None,
    )


async def _run_canary(
    http: httpx.AsyncClient,
    canary: CorrespondenceCanary,
    samples: int,
    pacer: _SamplePacer,
) -> dict:
    retrieval_results: list[bool] = []
    answer_contract_results: list[bool] = []
    distilled_queries: list[str] = []
    detected_flags: list[bool] = []
    skipped_samples: list[str] = []
    for _ in range(samples):
        await pacer.wait()
        retrieval_ok, contract_ok, distilled, detected, skipped = (
            await _run_one_sample(http, canary)
        )
        if skipped is not None:
            skipped_samples.append(skipped)
            continue
        retrieval_results.append(retrieval_ok)
        answer_contract_results.append(contract_ok)
        distilled_queries.append(distilled)
        detected_flags.append(detected)

    if retrieval_results:
        summary = summarize_canary_samples(canary.id, retrieval_results)
        summary["retrieval_majority_pass"] = summary["majority_pass"]
        summary["answer_contract_passed"] = sum(answer_contract_results)
        summary["answer_contract_all_pass"] = all(answer_contract_results)
        summary["majority_pass"] = bool(
            summary["retrieval_majority_pass"]
            and summary["answer_contract_all_pass"]
        )
    else:
        # Sol delta-review Fix 5: every sample fell back to the raw query —
        # summarize_canary_samples correctly raises on an empty list (that
        # is the right behavior for its own contract), so handle the
        # all-invalid case here instead, as an explicit failure.
        summary = {
            "canary_id": canary.id,
            "total": 0,
            "passed": 0,
            "pass_rate": 0.0,
            "majority_pass": False,
            "retrieval_majority_pass": False,
            "answer_contract_passed": 0,
            "answer_contract_all_pass": False,
            "note": "all samples invalid (skipped) — no valid distillation attempt",
        }
    summary["expected_chunks"] = canary.expected_chunks
    summary["distilled_queries"] = distilled_queries
    summary["pasted_correspondence_detected"] = detected_flags
    summary["skipped_samples"] = skipped_samples
    return summary


async def run(suite_path: Path, samples: int) -> list[dict]:
    canaries = load_pasted_correspondence_canaries(suite_path)
    if not canaries:
        print(
            f"No pasted_correspondence canaries found in {suite_path}", file=sys.stderr
        )
        return []

    pacer = _SamplePacer(delay_seconds=(1.0 / DIRECT_MISTRAL_RATE_LIMIT_RPS) + 1.0)
    async with httpx.AsyncClient(
        timeout=_RETRIEVE_TIMEOUT, headers=retrieve_headers()
    ) as http:
        return [await _run_canary(http, canary, samples, pacer) for canary in canaries]


def _print_report(summaries: list[dict]) -> bool:
    all_pass = True
    any_skipped = False
    for summary in summaries:
        skipped_samples = summary.get("skipped_samples", [])
        if skipped_samples:
            any_skipped = True

        status = "PASS" if summary["majority_pass"] else "FAIL"
        if not summary["majority_pass"]:
            all_pass = False
        note = summary.get("note")
        if note:
            print(f"[{status}] {summary['canary_id']}: {note}")
        else:
            print(
                f"[{status}] {summary['canary_id']}: "
                f"retrieval={summary['passed']}/{summary['total']} "
                f"answer_contract={summary['answer_contract_passed']}/"
                f"{summary['total']} "
                f"(pass_rate={summary['pass_rate']:.2f})"
            )
        for query, detected in zip(
            summary["distilled_queries"], summary["pasted_correspondence_detected"]
        ):
            print(f"    pasted_correspondence_detected={detected} distilled: {query!r}")
        if skipped_samples:
            print(
                f"    WARNING: {len(skipped_samples)} skipped sample(s) "
                f"excluded from pass rate: {skipped_samples}"
            )

    if any_skipped:
        print(
            "WARNING: one or more samples were skipped (raw-query fallback) "
            "during this run — see per-canary detail above.",
            file=sys.stderr,
        )
    return all_pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=_DEFAULT_SUITE,
        help=(
            "Path to the chat.yaml-shaped eval suite (default: "
            "$PASTED_CORRESPONDENCE_EVAL_SUITE or the container-mounted suite)"
        ),
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=3,
        help="Number of repeated distill+retrieve samples per canary (default: 3)",
    )
    args = parser.parse_args()

    summaries = asyncio.run(run(args.suite, args.samples))
    if not summaries:
        return 1

    all_pass = _print_report(summaries)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
