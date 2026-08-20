"""Live Phase 0 harness for SPEC-PRIVACY-MISTRAL-PII-001 (REQ-0a, REQ-0b).

Answers the two questions Phase 0 exists to settle, with a measurement:

  REQ-0a: does LiteLLM v1.96.2's native Presidio guardrail
  (``output_parse_pii: true``, ``config.yaml``'s ``presidio-pii-phase0``
  guardrail) correctly restore masked ``PERSON`` / ``PHONE_NUMBER`` /
  ``EMAIL_ADDRESS`` values on OUR stack, for both non-streaming and
  streaming responses? Probes the two failure shapes named in
  `BerriAI/litellm#6247 <https://github.com/BerriAI/litellm/issues/6247>`_
  explicitly: a corrupted map (restored value != original) and an empty
  map (nothing restored, placeholder still visible).

  REQ-0b: what is the Dutch token-survival rate — does the model echo a
  placeholder verbatim often enough for reversible masking to be usable
  for drafting? Runs >= 30 Dutch drafting prompts (write an email,
  summarise a call, draft a reply), each carrying a person name and a
  phone number, TWICE — with and without a system instruction telling the
  model to reproduce placeholder tokens verbatim — and reports survival
  per entity type, split into the three failure kinds REQ-0b names:
  not returned at all / returned altered / returned paraphrased.

All classification logic (the two REQ-0a failure shapes, the REQ-0b
survival taxonomy, the Dutch prompt corpus) is pure and network-free in
``klai_pii_restore_eval.py``, unit-tested with no network calls in
``tests/test_pii_restore_eval.py``, which DOES run in normal CI. This
script is the thin orchestration layer that calls the real, local LiteLLM
proxy — mirroring ``scripts/eval_pasted_correspondence_live.py``'s split
and its ``_SamplePacer`` inter-call pacing pattern.

THIS SCRIPT CANNOT RUN IN STANDARD CI. It needs:
  - Real Mistral quota through the local LiteLLM proxy.
  - The ``presidio-pii-phase0`` guardrail registered (``config.yaml``) and
    both Presidio containers (``presidio-analyzer``, ``presidio-anonymizer``
    in ``docker-compose.yml``) up and reachable.
  - ``LITELLM_MASTER_KEY`` set (used as the request's bearer token, same as
    every other in-container caller — see ``klai_kb_query_rewrite.py``'s
    ``QUERY_REWRITE_API_KEY``).

Run it explicitly on the server:

    docker exec klai-core-litellm-1 python scripts/eval_pii_restore_live.py

Rate limiting: every call in this script goes through the SAME local
LiteLLM proxy loopback (``http://127.0.0.1:4000``) that
``klai_kb_query_rewrite.py`` and ``eval_pasted_correspondence_live.py``
already use — NOT a direct call to ``api.mistral.ai`` (that bypass is
exactly what ``tests/test_direct_mistral_throttle_drift.py`` guards
against for this directory). Calling through the proxy means every request
is already accounted against the ``klai-fast`` alias's own RPM/TPM budget
(``rpm: 45`` / ``tpm: 45000`` in ``config.yaml``, enforced by
``router_settings.optional_pre_call_checks: enforce_model_rate_limits``) —
the same shared accounting every other in-process caller uses, so this
harness cannot silently exceed the alias budget the way an uncoordinated
direct-Mistral caller could (the known incident class this repo's rate-
limiting rules exist to prevent). On top of that router-level ceiling,
``_SamplePacer`` adds an explicit inter-call delay (default 2s, matching
``eval_pasted_correspondence_live.py``'s default) so a full Phase 0 run
stays well under the alias's 45 rpm even before the router's own queueing
kicks in.

Note: this directory does NOT currently vendor ``klai_llm_throttle``
(``klai-libs/llm-throttle``) or import a ``direct_mistral_limiter`` /
``shared_klai_fast_limiter`` singleton — those are knowledge-ingest's
process-wide token buckets for its OWN direct callers
(``knowledge_ingest/llm_throttle.py``), and ``klai_kb_query_rewrite.py``'s
own Mistral calls already go through the local proxy rather than a
separate direct client. Routing through the same loopback proxy + alias
budget as every other ``deploy/litellm`` caller is the applicable
precedent here, not importing a sibling service's client-side limiter.

Detection precondition: since ``presidio_language`` is necessarily ``"en"``
in Phase 0 (REQ-2's Dutch NLP engine is Phase 1 work), the English analyzer
may fail to detect a Dutch name in some prompts. If it never detects an
entity, that entity is never masked, and a verbatim echo of the (unmasked)
original value would look identical to a successful restore. Before
scoring any round trip as restore evidence, this script calls the
analyzer's own ``/analyze`` endpoint directly (``PRESIDIO_ANALYZER_API_BASE``,
same internal service the guardrail itself uses) to confirm each entity
was actually detected in that exact prompt text, and tags every probe/
sample with the result (``masked_by_analyzer``). An entity the analyzer
never detected is reported in its own ``not_masked`` bucket rather than
silently counted as a pass or a survival failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Literal

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from klai_pii_restore_eval import (  # noqa: E402
    RestoreProbe,
    SurvivalCondition,
    SurvivalSample,
    VERBATIM_TOKEN_SYSTEM_INSTRUCTION,
    classify_token_survival,
    dutch_drafting_prompts,
    summarize_restore_probes,
    summarize_token_survival,
)

# Both loops below iterate literal tuples whose first element must stay narrow:
# RestoreProbe.mode and SurvivalSample.condition are Literal types, and a bare
# tuple literal widens to `str` under a type checker, so annotate the sequences.
_ProbeMode = Literal["streaming", "non_streaming"]

PII_EVAL_URL = os.getenv(
    "PII_EVAL_LITELLM_URL", "http://127.0.0.1:4000/v1/chat/completions"
)
PII_EVAL_MODEL = os.getenv("PII_EVAL_MODEL", "klai-fast")
PII_EVAL_API_KEY = os.getenv("LITELLM_MASTER_KEY", "")
PII_EVAL_GUARDRAIL_NAME = os.getenv(
    "PII_EVAL_GUARDRAIL_NAME", "presidio-pii-phase0"
)
PII_EVAL_SAMPLE_DELAY_SECONDS = float(
    os.getenv("PII_EVAL_SAMPLE_DELAY_SECONDS", "2.0")
)
# Same internal service name the guardrail itself uses (config.yaml /
# docker-compose.yml) — reachable from inside the litellm container without
# going through Mistral quota, so no pacing needed for these calls.
PRESIDIO_ANALYZER_API_BASE = os.getenv(
    "PRESIDIO_ANALYZER_API_BASE", "http://presidio-analyzer:3000"
)
_REQUEST_TIMEOUT = 45.0
_ANALYZER_TIMEOUT = 15.0

_REQ0A_PERSON = "Jan de Vries"
_REQ0A_PHONE = "06-12345678"
_REQ0A_EMAIL = "jan.devries@example.nl"
_REQ0A_PROMPT = (
    "Herhaal exact de volgende contactgegevens terug, woord voor woord, "
    f"zonder er iets aan te veranderen: naam {_REQ0A_PERSON}, telefoon "
    f"{_REQ0A_PHONE}, e-mail {_REQ0A_EMAIL}."
)


class _SamplePacer:
    """Sleep before every call except the first across the entire run.

    Mirrors ``eval_pasted_correspondence_live.py``'s ``_SamplePacer`` —
    same pattern, same purpose: avoid turning a manual eval into a traffic
    burst against a quota shared with production.
    """

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self._started = False

    async def wait(self) -> None:
        if self._started:
            await asyncio.sleep(self._delay_seconds)
        self._started = True


def _headers() -> dict:
    if not PII_EVAL_API_KEY:
        raise RuntimeError(
            "LITELLM_MASTER_KEY is not set — required to call the local "
            "LiteLLM proxy (see module docstring)."
        )
    return {
        "Authorization": f"Bearer {PII_EVAL_API_KEY}",
        "Content-Type": "application/json",
    }


async def _confirm_entities_masked(
    http: httpx.AsyncClient, text: str, entity_types: list[str]
) -> dict[str, bool]:
    """REQ-0a/REQ-0b precondition: call the analyzer directly to confirm it
    actually detects each entity type in ``text`` BEFORE trusting a
    round-trip probe as restore/survival evidence.

    Without this, a Dutch name the English (Phase 0) analyzer never
    detects reaches the model unmasked, and a verbatim echo of the real
    value would be indistinguishable from a successful restore — see the
    module docstring's "Detection precondition" section.
    """
    url = PRESIDIO_ANALYZER_API_BASE.rstrip("/") + "/analyze"
    resp = await http.post(
        url,
        json={"text": text, "language": "en"},
        timeout=_ANALYZER_TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json()
    if not isinstance(results, list):
        raise TypeError("presidio-analyzer /analyze returned a non-list response")
    detected = {
        item.get("entity_type") for item in results if isinstance(item, dict)
    }
    return {entity_type: entity_type in detected for entity_type in entity_types}


async def _call_non_streaming(http: httpx.AsyncClient, messages: list[dict]) -> str:
    payload = {
        "model": PII_EVAL_MODEL,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 400,
        "guardrails": [PII_EVAL_GUARDRAIL_NAME],
    }
    resp = await http.post(PII_EVAL_URL, json=payload, headers=_headers())
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise TypeError("PII eval non-streaming call returned non-string content")
    return content


async def _call_streaming(http: httpx.AsyncClient, messages: list[dict]) -> str:
    payload = {
        "model": PII_EVAL_MODEL,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 400,
        "guardrails": [PII_EVAL_GUARDRAIL_NAME],
        "stream": True,
    }
    chunks: list[str] = []
    async with http.stream(
        "POST", PII_EVAL_URL, json=payload, headers=_headers()
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:") :].strip()
            if not data_str or data_str == "[DONE]":
                continue
            event = json.loads(data_str)
            choices = event.get("choices") or []
            if not choices:
                continue
            delta_content = choices[0].get("delta", {}).get("content")
            if delta_content:
                chunks.append(delta_content)
    return "".join(chunks)


async def run_req0a(http: httpx.AsyncClient, pacer: _SamplePacer) -> dict:
    """AC-0a (non-streaming) + AC-0b (streaming): restore round-trip."""
    messages = [{"role": "user", "content": _REQ0A_PROMPT}]

    entities = [
        ("PERSON", _REQ0A_PERSON),
        ("PHONE_NUMBER", _REQ0A_PHONE),
        ("EMAIL_ADDRESS", _REQ0A_EMAIL),
    ]
    # Precondition (Sol delta-review finding): confirm the analyzer actually
    # detects all three entities in the prompt BEFORE trusting the round
    # trip as restore evidence — a Dutch name the English analyzer misses
    # would reach the model unmasked, and its verbatim echo would look
    # identical to a successful restore.
    masked = await _confirm_entities_masked(
        http, _REQ0A_PROMPT, [entity_type for entity_type, _ in entities]
    )

    await pacer.wait()
    non_streaming_text = await _call_non_streaming(http, messages)
    await pacer.wait()
    streaming_text = await _call_streaming(http, messages)

    probe_modes: tuple[tuple[_ProbeMode, str], ...] = (
        ("non_streaming", non_streaming_text),
        ("streaming", streaming_text),
    )
    probes = [
        RestoreProbe(
            mode=mode,
            entity_type=entity_type,
            original_value=original_value,
            response_text=response_text,
            masked_by_analyzer=masked.get(entity_type, False),
        )
        for mode, response_text in probe_modes
        for entity_type, original_value in entities
    ]
    summary = summarize_restore_probes(probes)
    summary["non_streaming_raw"] = non_streaming_text
    summary["streaming_raw"] = streaming_text
    summary["masked_by_analyzer"] = masked
    return summary


async def run_req0b(http: httpx.AsyncClient, pacer: _SamplePacer) -> list[SurvivalSample]:
    """REQ-0b: Dutch token-survival rate, with and without the verbatim
    instruction."""
    prompts = dutch_drafting_prompts()
    samples: list[SurvivalSample] = []

    # Same precondition as run_req0a, checked once per prompt (not per
    # condition — the system instruction added by with_instruction does not
    # change what the analyzer detects in the user turn). Local calls to
    # presidio-analyzer, no Mistral quota, no pacing needed.
    masked_by_prompt: dict[str, dict[str, bool]] = {
        prompt.id: await _confirm_entities_masked(
            http, prompt.text, ["PERSON", "PHONE_NUMBER"]
        )
        for prompt in prompts
    }

    conditions: tuple[tuple[SurvivalCondition, bool], ...] = (
        ("without_instruction", False),
        ("with_instruction", True),
    )
    for condition, use_instruction in conditions:
        for prompt in prompts:
            await pacer.wait()
            messages: list[dict] = []
            if use_instruction:
                messages.append(
                    {"role": "system", "content": VERBATIM_TOKEN_SYSTEM_INSTRUCTION}
                )
            messages.append({"role": "user", "content": prompt.text})
            content = await _call_non_streaming(http, messages)

            masked = masked_by_prompt[prompt.id]
            for entity_type, original_value in (
                ("PERSON", prompt.person),
                ("PHONE_NUMBER", prompt.phone),
            ):
                outcome = classify_token_survival(
                    response_text=content,
                    entity_type=entity_type,
                    original_value=original_value,
                    masked_by_analyzer=masked.get(entity_type, False),
                )
                samples.append(
                    SurvivalSample(
                        prompt_id=prompt.id,
                        condition=condition,
                        entity_type=entity_type,
                        outcome=outcome,
                    )
                )

    return samples


def _print_req0a_report(summary: dict) -> bool:
    print("=== REQ-0a: restore round-trip ===")
    any_not_masked = False
    for row in summary["probes"]:
        if row["outcome"] == "exact_match":
            status = "PASS"
        elif row["outcome"] == "not_masked":
            status = "SKIP"
            any_not_masked = True
        else:
            status = "FAIL"
        print(f"[{status}] {row['mode']:>13} {row['entity_type']:<14} {row['outcome']}")
    if any_not_masked:
        print(
            "WARNING: the analyzer did not detect one or more REQ-0a probe "
            'entities in the outbound prompt (presidio_language is "en" in '
            "Phase 0 — Dutch NLP support is Phase 1, REQ-2). Those probes "
            "are inconclusive, not evidence either way, but still count "
            "against all_pass below so incomplete data cannot silently "
            "report a pass. Pick probe values the English analyzer "
            "reliably detects and re-run.",
            file=sys.stderr,
        )
    if not summary["all_pass"]:
        print(
            "WARNING: restore did NOT survive for every probe — REQ-0a's "
            "answer is negative. Per the SPEC, REQ-8 falls back to the "
            "own-hook (async_post_call_streaming_iterator_hook / "
            "async_post_call_success_hook) implementation, and that cost "
            "must be recorded in the SPEC before Phase 1 proceeds.",
            file=sys.stderr,
        )
    return summary["all_pass"]


def _print_req0b_report(samples: list[SurvivalSample]) -> None:
    print("\n=== REQ-0b: Dutch token survival ===")
    report = summarize_token_survival(samples)
    for entity_type, by_condition in report.items():
        for condition, stats in by_condition.items():
            rate = stats["survival_rate"]
            rate_str = f"{rate:.2%}" if rate is not None else "n/a"
            gate = "BELOW 95%" if stats["below_95_percent_gate"] else "ok"
            print(
                f"{entity_type:<14} {condition:<19} "
                f"survived={stats['survived']}/{stats['scored_total']} "
                f"({rate_str}, {gate}) "
                f"not_masked={stats['not_masked']} "
                f"not_returned={stats['not_returned']} "
                f"altered={stats['altered']} "
                f"paraphrased={stats['paraphrased']}"
            )


async def run(*, skip_req0a: bool, skip_req0b: bool) -> int:
    pacer = _SamplePacer(delay_seconds=PII_EVAL_SAMPLE_DELAY_SECONDS)
    exit_code = 0
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as http:
        if not skip_req0a:
            req0a_summary = await run_req0a(http, pacer)
            req0a_passed = _print_req0a_report(req0a_summary)
            if not req0a_passed:
                exit_code = 1
        if not skip_req0b:
            samples = await run_req0b(http, pacer)
            _print_req0b_report(samples)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-req0a", action="store_true", help="Skip the restore round-trip probe"
    )
    parser.add_argument(
        "--skip-req0b",
        action="store_true",
        help="Skip the Dutch token-survival run (30 prompts x 2 conditions)",
    )
    args = parser.parse_args()
    return asyncio.run(run(skip_req0a=args.skip_req0a, skip_req0b=args.skip_req0b))


if __name__ == "__main__":
    raise SystemExit(main())
