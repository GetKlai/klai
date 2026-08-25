"""KlaiPiiObserver — Phase 2 read-only PII observer for the Mistral call path.

SPEC-PRIVACY-MISTRAL-PII-001 REQ-5/REQ-6. Registered as the LAST entry in
``config.yaml``'s ``litellm_settings.callbacks`` list, so it runs after
``klai_knowledge.klai_knowledge_hook`` (KB context / attachment injection)
and ``custom_router.token_router`` (model upgrade/downgrade) — it observes
the actual outbound payload, not the original request.

Correction (SPEC 0.4.0): an earlier draft of REQ-5 specified the native
LiteLLM Presidio guardrail in ``mode: "logging_only"``. That mode masks
what reaches observability platforms (Langfuse etc.) and sends the
payload to the provider UNMASKED — inverted for measurement purposes.
LiteLLM's Presidio guardrail has no detect-only mode at all. This module
is the read-only observer that replaces that plan: it calls the
analyzer directly, counts detections, and NEVER touches the payload.

Contract (REQ-5):
  - Calls presidio-analyzer's ``/analyze`` with the outbound message text.
  - Returns ``data`` completely unchanged. Never calls the anonymizer.
  - Does NOT honour ``_klai_openai_passthrough`` or missing ``org_id`` —
    the two blind spots in ``KlaiKnowledgeHook`` (``klai_knowledge.py``
    :426-427, :481-483) that this phase exists to see (AC-7, AC-8).
  - Inspects every ``content`` field of every message, not just the last
    user turn.
  - Runs OUT OF BAND: the analyzer call is scheduled via
    ``asyncio.create_task`` and never awaited by the hook itself, so it
    cannot add latency to the user's request. A timeout or error inside
    the scheduled task is caught, logged at ``warning``, and swallowed —
    it can never fail or delay the request. This mirrors the existing
    fire-and-forget pattern in ``klai_retrieval_telemetry.py``
    (``fire_retrieval_log`` / ``fire_gap_event``): build the coroutine
    only as the argument to ``create_task`` so a missing running loop
    (test/sync context) raises before any coroutine object is created —
    no "coroutine was never awaited" warning at GC.

Contract (REQ-6): the emitted event carries ``org_id``, ``call_type``,
model alias, detected language, and a COUNT per entity type. It never
carries a matched value, surrounding text, a character offset, or a hash
of a matched value — REQ-6's own reasoning is that a hash of a BSN is a
BSN (nine digits is a trivially brute-forceable search space). The log
call below passes only: an opaque ``org_id``, the ``call_type`` string
LiteLLM itself supplies, the routed model alias, a two/three-letter
language code, and ``{entity_type: count}`` — no code path in this
module ever puts a message string, a slice of one, an offset, or a
digest into a log argument.

Deleted in the Phase 3 PR (REQ-5's own instruction): once the native
guardrail enforces, a second path evaluating the same payload is
duplicate machinery ("clean over clever, no parallel old+new").
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import Counter
from typing import Any

import httpx
from litellm.integrations.custom_logger import CustomLogger

from klai_kb_request_context import message_text as _message_text
from klai_language_detect import UNKNOWN_LANGUAGE, detect_language

logger = logging.getLogger(__name__)

# Same internal service name the Phase 0/1 guardrail and harness already use
# (config.yaml's `presidio-pii-phase0` guardrail, docker-compose.yml, and
# scripts/eval_pii_restore_live.py). No secret: neither Presidio container
# has auth of its own.
PRESIDIO_ANALYZER_API_BASE = os.getenv(
    "PRESIDIO_ANALYZER_API_BASE", "http://presidio-analyzer:3000"
)

# Out-of-band call: kept short on purpose. This is a background task the
# user's request never waits on, but an unbounded call here would still
# pile up concurrent tasks under load and eventually starve the analyzer
# for its OTHER caller (the Phase 0 harness / a future Phase 3 guardrail).
# `asyncio.wait_for` is the real deadline; the httpx client timeout is the
# safety net underneath it (see .claude/rules/klai/lang/python.md).
_ANALYZER_CALL_TIMEOUT_SECONDS = 3.0
_HTTPX_CLIENT_TIMEOUT_SECONDS = 5.0

# deploy/presidio/analyzer/conf/analyzer.yaml `supported_languages` (REQ-2,
# Phase 1). None of the entities this pack detects are language-scoped —
# every one is regex-plus-checksum — so this only selects which /analyze
# call the analyzer accepts vs. rejects with a language error; it is NOT a
# detection-quality knob for anything except a future PERSON/GLiNER
# recognizer this phase does not have yet.
_ANALYZER_SUPPORTED_LANGUAGES = frozenset({"en", "nl", "de"})
_DEFAULT_ANALYZER_LANGUAGE = "en"

# ---------------------------------------------------------------------------
# Length cap on analysed text — system-review finding M4
# ---------------------------------------------------------------------------
# presidio-analyzer runs with `cpus: '1'`, shared by every tenant, and this
# observer already sends it the FULL outbound payload with no size cap at
# all — one pasted payload shaped to be expensive for any recognizer (the
# PEM pattern's own quadratic-on-unmatched-markers cost, fixed separately in
# klai_pii_recognizers.py, was one concrete way to do that) pegs the shared
# core for every tenant's request queued behind it. Even though this hook is
# fire-and-forget and its own timeout eventually gives up client-side, the
# analyzer process keeps chewing CPU on the call already in flight — a
# client-side timeout does not cancel server-side work.
#
# 20,000 chars, same value and same basis as klai_pii_enforce.py's
# `_MAX_ANALYZE_CHARS` (double the NFR's own 10,000-char latency reference
# payload) — kept in sync manually since Phase 2 and Phase 3 are separate,
# independently-owned modules per REQ-5's "no parallel old+new" boundary.
#
# Unlike the enforce path, truncating here is the right call, not a
# shortcut: REQ-5 says this observer "SHALL return the payload unchanged"
# and "SHALL NOT" affect what reaches Mistral — it exists purely to COUNT
# detections for telemetry (REQ-6), fails open on any error already, and
# Phase 2's own measurement is explicitly directional, not a gate (AC-14).
# Analysing only the first `_MAX_ANALYZE_CHARS` characters of an oversized
# payload can only under-count that one telemetry sample; it can never
# change what the user's request does. A `truncated` flag rides along in
# the emitted event (REQ-6 allows it — it is a boolean, not a value, an
# offset, or a hash) so the undercount is visible in the data rather than
# silent.
_MAX_ANALYZE_CHARS = 20_000


# ---------------------------------------------------------------------------
# Analyzer call
# ---------------------------------------------------------------------------
def _payload_texts(messages: list[Any]) -> list[str]:
    """Every non-empty ``content`` field across every message.

    Uses the same ``message_text`` helper klai_knowledge.py's own
    request-context parsing already relies on, so both string content and
    multi-part (list-of-parts) content are handled identically — no
    reimplementation of that shape-handling here.
    """
    texts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        text = _message_text(message)
        if text:
            texts.append(text)
        # An assistant turn in an agentic flow can carry content=None while
        # tool_calls[].function.arguments holds an email address, a BSN, or
        # anything else the model decided to pass along. The router keeps
        # those turns and they go to Mistral verbatim, so a measurement that
        # reads only `content` under-reports exactly the agentic paths
        # (klai-large, MCP) where PII is most likely to be moving around.
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            arguments = (call.get("function") or {}).get("arguments")
            if isinstance(arguments, str) and arguments.strip():
                texts.append(arguments)
    return texts


def _user_text(messages: list[Any]) -> str:
    """Text of the latest user turn, for language detection only.

    Detecting on the whole payload would classify almost everything as
    English: the KB context block is deliberately English-structured
    (SPEC-RAG-MULTILINGUAL-CHAT-001) and usually dwarfs the question. The
    language dimension exists so REQ-2's per-language recall can be
    compared, and a field that says "en" for a Dutch question is worse
    than no field at all. The PII scan itself still covers the full
    payload — only the language label is narrowed.
    """
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _message_text(message)
        if text:
            return text
    return ""


async def _analyze_entity_counts(
    http: httpx.AsyncClient, text: str, language: str
) -> Counter[str]:
    """Call presidio-analyzer's ``/analyze`` and count hits per entity type.

    Deliberately discards everything /analyze returns except
    ``entity_type`` — REQ-6 forbids emitting the matched value, its
    offset, or any surrounding text, and this is the one place in the
    module that ever sees the analyzer's raw (potentially
    value-adjacent) response, so it is also the one place that must never
    forward more of it than a bare count.
    """
    url = PRESIDIO_ANALYZER_API_BASE.rstrip("/") + "/analyze"
    response = await asyncio.wait_for(
        http.post(url, json={"text": text, "language": language}),
        timeout=_ANALYZER_CALL_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    results = response.json()
    if not isinstance(results, list):
        raise TypeError("presidio-analyzer /analyze returned a non-list response")

    counts: Counter[str] = Counter()
    for item in results:
        if not isinstance(item, dict):
            continue
        entity_type = item.get("entity_type")
        if isinstance(entity_type, str) and entity_type:
            counts[entity_type] += 1
    return counts


def _org_id_from_key(user_api_key_dict: Any) -> Any:
    """Best-effort org_id, WITHOUT the KlaiKnowledgeHook early-return.

    ``klai_knowledge.py`` treats a missing org_id as "master key usage,
    skip silently" (:481-483) — every widget and partner request, since
    ``partner_chat.py`` calls with the master key and no ``user`` field.
    That is exactly the blind spot AC-8 tests: this observer reports
    ``None`` for org_id rather than skipping the request.
    """
    metadata = getattr(user_api_key_dict, "metadata", {}) or {}
    return metadata.get("org_id")


async def _observe(
    combined_text: str,
    *,
    language: str,
    org_id: Any,
    call_type: str,
    model: Any,
) -> None:
    """Do the analyzer call + emit telemetry. Runs as a background task.

    Every exception, including a timeout, is caught here and logged at
    ``warning`` rather than re-raised — REQ-5's "a failure or timeout in
    it SHALL NOT fail the request", the deliberate inverse of REQ-10
    (which governs Phase 3 enforcement, not this observer).
    """
    # REQ-6 permits a boolean here (no value, no offset, no hash): it only
    # says whether the sample below is a full or a partial count, not what
    # was cut. See the module-level comment above `_MAX_ANALYZE_CHARS`.
    truncated = len(combined_text) > _MAX_ANALYZE_CHARS
    analyzed_text = combined_text[:_MAX_ANALYZE_CHARS] if truncated else combined_text

    try:
        analyzer_language = (
            language
            if (
                language != UNKNOWN_LANGUAGE
                and language in _ANALYZER_SUPPORTED_LANGUAGES
            )
            else _DEFAULT_ANALYZER_LANGUAGE
        )
        async with httpx.AsyncClient(timeout=_HTTPX_CLIENT_TIMEOUT_SECONDS) as http:
            counts = await _analyze_entity_counts(http, analyzed_text, analyzer_language)
    except Exception as exc:
        # Fail-open per REQ-5, but keep the same non-sensitive dimensions the
        # success event carries. Without them an outage cannot be filtered out
        # of the Phase 2 sample per tenant/model/language, which quietly turns
        # "Presidio was down for this org" into "this org has no PII".
        logger.warning(
            "pii_observe_failed org_id=%s call_type=%s model=%s language=%s truncated=%s error=%s",
            org_id,
            call_type,
            model,
            language,
            truncated,
            exc,
        )
        return

    # REQ-6: org_id, call_type, model alias, detected language, per-entity
    # counts. Nothing else — no message text, no offsets, no hashes.
    logger.warning(
        "pii_observed org_id=%s call_type=%s model=%s language=%s truncated=%s entity_counts=%s",
        org_id,
        call_type,
        model,
        language,
        truncated,
        dict(counts),
    )


class KlaiPiiObserver(CustomLogger):
    """Read-only PII observer. Never mutates or blocks the request."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        messages = data.get("messages")
        try:
            if isinstance(messages, list) and messages:
                org_id = _org_id_from_key(user_api_key_dict)
                model = data.get("model")
                # Build the coroutine only as create_task's argument: if
                # get_running_loop() raises (no running loop — sync/test
                # context), the coroutine is never constructed and no
                # "never awaited" warning fires at GC. Mirrors
                # klai_retrieval_telemetry.py's fire_retrieval_log /
                # fire_gap_event.
                # Snapshot the text SYNCHRONOUSLY, before scheduling. The
                # background task must not read `messages` by reference:
                # klai_knowledge.py mutates data["messages"] (history
                # sanitisation, system prefixes, KB context injection), so a
                # deferred read would measure whatever the list happens to
                # hold when the loop gets round to the task. Same list,
                # different content, no error — the measurement would just
                # quietly drift. An immutable string fixes the measurement
                # point at this callback's position, which is deliberately
                # last in `callbacks:` so it is the post-injection payload.
                texts = _payload_texts(messages)
                if texts:
                    language = detect_language(_user_text(messages))
                    asyncio.get_running_loop().create_task(
                        _observe(
                            "\n\n".join(texts),
                            language=language,
                            org_id=org_id,
                            call_type=call_type,
                            model=model,
                        )
                    )
        except Exception as exc:
            # Scheduling itself must never fail the request either — this
            # is the observer's own version of REQ-5's fail-open contract,
            # one layer up from _observe's internal try/except.
            logger.warning("pii_observe_schedule_failed error=%s", exc)
        return data


klai_pii_observer = KlaiPiiObserver()
