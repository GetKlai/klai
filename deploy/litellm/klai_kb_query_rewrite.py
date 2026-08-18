"""Query rewrite and taxonomy classification helpers for LiteLLM KB retrieval.

Environment-derived constants in this module are boot-time configuration:
production imports the LiteLLM hook once per process, and runtime env toggles
take effect on process restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

import httpx

from klai_citations import (
    is_followup_query as _is_followup_query,
    rewrite_preserves_subject as _rewrite_preserves_current_query,
    salient_tokens as _rewrite_salient_tokens,
)
from klai_llm_throttle import TokenBucketLimiter

logger = logging.getLogger("klai_knowledge")

QUERY_REWRITE_ENABLED = os.getenv("QUERY_REWRITE_ENABLED", "true").lower() == "true"
QUERY_REWRITE_TIMEOUT = float(os.getenv("QUERY_REWRITE_TIMEOUT", "1.5"))
QUERY_REWRITE_MODEL = os.getenv("QUERY_REWRITE_MODEL", "mistral-small-2603")
QUERY_REWRITE_HISTORY_TURNS = int(os.getenv("QUERY_REWRITE_HISTORY_TURNS", "4"))
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

# 2026-08-18 incident: this module calls Mistral directly, bypassing the
# litellm proxy's OWN rpm accounting for the klai-fast/klai-primary aliases
# entirely (see klai-libs/llm-throttle/README.md for the full incident —
# this was the SAME class of bug as the 2026-08-14 knowledge-ingest
# incident, invisible to that fix because it lived in a different
# process/package). Every direct Mistral call in this module MUST acquire
# from this shared limiter first.
#
# Rate is a deliberately conservative STARTING point, not a precisely
# derived figure. config.yaml pins klai-primary + klai-fast to rpm: 45 each
# (90 rpm total) against mistral-small-2603's 100 RPM upstream cap — but
# that 90 rpm is enforced by the LiteLLM router for calls THROUGH the proxy;
# this module's direct-to-api.mistral.ai calls are invisible to it. A THIRD,
# uncoordinated consumer of the same 100 rpm account cap must therefore stay
# small: at the default below, worst case is 90 + 6 = 96 rpm, a 4 rpm margin.
# litellm runs as a single container (no replicas in docker-compose.yml), so
# this process-local bucket is the only consumer of this budget — no
# multi-instance multiplication risk today. If that ever changes (replicas,
# scaled deployment), this in-process bucket stops being a real ceiling and
# needs a shared (e.g. Redis-backed) limiter instead. Tune via
# DIRECT_MISTRAL_RATE_LIMIT_RPS/_BURST against the observed 429 rate
# (Grafana "RAG Quality" panel / VictoriaLogs
# `service:litellm AND "RouterRateLimitError"`) rather than assuming this
# default is correct — it has not been load-tested against real production
# volume.
DIRECT_MISTRAL_RATE_LIMIT_RPS = float(os.getenv("DIRECT_MISTRAL_RATE_LIMIT_RPS", "0.1"))
DIRECT_MISTRAL_RATE_LIMIT_BURST = float(
    os.getenv("DIRECT_MISTRAL_RATE_LIMIT_BURST", "3")
)

_direct_mistral_limiter_singleton: TokenBucketLimiter | None = None


def direct_mistral_limiter() -> TokenBucketLimiter:
    """Process-wide limiter for every direct (non-proxied) Mistral call in
    this module. Lazy singleton — same pattern as
    knowledge_ingest.llm_throttle.shared_klai_fast_limiter, sharing the same
    underlying klai_llm_throttle.TokenBucketLimiter implementation.
    """
    global _direct_mistral_limiter_singleton
    if _direct_mistral_limiter_singleton is None:
        _direct_mistral_limiter_singleton = TokenBucketLimiter(
            rate=DIRECT_MISTRAL_RATE_LIMIT_RPS,
            capacity=DIRECT_MISTRAL_RATE_LIMIT_BURST,
        )
    return _direct_mistral_limiter_singleton


async def _post_to_mistral_throttled(
    payload: dict,
    headers: dict,
    client_kwargs: dict,
    timeout: float,
) -> httpx.Response:
    """Rate-limit acquire + HTTP POST, bounded by ONE total timeout.

    Without this wrapper, ``acquire()`` can block past ``QUERY_REWRITE_TIMEOUT``
    on its own (e.g. waiting out the token bucket under load), so the total
    call time is unbounded even though ``client_kwargs["timeout"]`` only
    bounds the HTTP leg. A timeout here is treated like any other rewrite
    failure by the caller — fail-open to the raw query.
    """

    async def _call() -> httpx.Response:
        await direct_mistral_limiter().acquire()
        async with httpx.AsyncClient(**client_kwargs) as client:
            return await client.post(MISTRAL_API_URL, json=payload, headers=headers)

    return await asyncio.wait_for(_call(), timeout=timeout)


KNOWLEDGE_RETRIEVE_URL = os.getenv("KNOWLEDGE_RETRIEVE_URL")
RETRIEVAL_INTERNAL_SECRET = os.getenv("RETRIEVAL_INTERNAL_SECRET") or os.getenv(
    "PORTAL_INTERNAL_SECRET"
)
_RETRIEVAL_BASE_URL = (KNOWLEDGE_RETRIEVE_URL or "").rsplit("/retrieve", 1)[0]
TAXONOMY_ENABLED = os.getenv("TAXONOMY_ENABLED", "true").lower() == "true"
KLAI_TAXONOMY_COVERAGE_THRESHOLD = float(
    os.getenv("KLAI_TAXONOMY_COVERAGE_THRESHOLD", "0.30")
)
TAXONOMY_FETCH_TIMEOUT = float(os.getenv("TAXONOMY_FETCH_TIMEOUT", "0.8"))

_QUERY_REWRITE_PROMPT = (
    "You are a query rewriter for a RAG search system. Rewrite the user's "
    "current question so it makes sense as a stand-alone search query — "
    "resolve pronouns and references using the conversation history. If the "
    "question is already clear and self-contained, return it unchanged.\n\n"
    "The rewrite MUST keep the subject of the user's CURRENT question. "
    "History may only supply referents for pronouns, ellipsis, or follow-up "
    "phrases — never replace the current question's topic with a topic from "
    "history. When the current question introduces a new topic, ignore the "
    "history and return the question unchanged.\n\n"
    "Brand-bridging: if the question mentions a third-party brand or product "
    "name (e.g. Salesforce, HubSpot, Pipedrive, Zoom, Microsoft Teams, "
    "Outlook), also include 2–4 broader category or related-brand terms in "
    "the rewritten query so search can find category-specific or partner-brand "
    "pages even when the original brand string is absent. If no third-party "
    "brand is mentioned, leave the rewrite unchanged beyond standard pronoun "
    "resolution.\n\n"
    "{distillation_block}"
    "Conversation history (oldest → newest):\n{history}\n\n"
    "User's current question: {raw_query}\n\n"
    "Reply with ONLY the rewritten question, no preamble, no explanation, "
    "no quotes. Maximum 200 characters. Same language as the user's input."
)

# SPEC-RAG-CORRESPONDENCE-DISTILL-001: inserted only when the caller detected
# pasted third-party correspondence (klai_pasted_correspondence.py) in the
# current turn. A long, noisy pasted email/ticket embedded almost verbatim as
# the retrieval query dilutes the embedding signal enough that an article
# which DOES answer the question fails to surface (2026-08-17 Voys incident,
# reproduced and root-caused in that SPEC's Motivation). This block asks the
# SAME rewrite call to distill the correspondence into a compact search
# query instead — no new LLM round-trip, no new latency dimension. Empty
# string when not flagged, so the formatted prompt is byte-identical to the
# pre-SPEC template (AC-3) whenever this instruction does not apply.
_PASTED_CORRESPONDENCE_DISTILL_BLOCK = (
    "Pasted correspondence detected: the user's current question contains "
    "pasted third-party correspondence (an email, ticket, or forwarded "
    "thread) with substantial noise. Distill it into a compact, "
    "self-contained search query describing the core technical or support "
    "question or problem — NOT the correspondence's conclusions or "
    "opinions. Preserve reusable domain terminology verbatim: error codes, "
    "protocol/status codes, product and technology names (e.g. 'SIP 404 "
    "Not Found', 'trunk', 'VoIP'). Do NOT preserve unique per-incident "
    "identifiers — Call-IDs, specific account/trunk/ticket numbers, IP "
    "addresses, phone numbers — these never appear in knowledge-base "
    "articles and pulling them into the search query points it away from "
    "the general topic instead of toward it. Output a short KEYWORD-STYLE "
    "phrase (like a search-engine query), NOT a full grammatical question "
    "or sentence — no question words, no markdown formatting, no "
    "punctuation beyond what a code or term itself requires. Drop mail "
    "headers, sender and recipient names and addresses, dates, greetings, "
    "signature blocks, and 'RE:'/'FW:' subject-chain noise.\n\n"
)

_QUERY_REWRITE_AND_CLASSIFY_PROMPT = (
    "You are a query rewriter and topic classifier for a RAG search system.\n\n"
    "Tasks (combined, single JSON response):\n"
    "1. Rewrite the user's current question into a self-contained search query "
    "— resolve pronouns and references using the conversation history. "
    "If already clear, return it unchanged. The rewrite MUST keep the subject "
    "of the CURRENT question: history may only supply referents for pronouns, "
    "ellipsis, or follow-up phrases — never replace the current question's "
    "topic with a topic from history. Example: history about Yealink "
    "toestellen, current question 'Wat weet je over klai?' → "
    "'Wat weet je over klai?' (new topic — history ignored).\n"
    "2. SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-5 — Brand-bridging: if the "
    "question mentions a third-party brand or product name (e.g. Salesforce, "
    "HubSpot, Pipedrive, Zoom, Microsoft Teams, Outlook), ALSO include "
    "2–4 broader category or related-brand terms in the rewritten query so "
    "the search can find category-specific or partner-brand pages even when "
    "the original brand string is absent from the source content. Stay "
    "within the 200-char limit; same language as the user.\n"
    "Examples:\n"
    "- 'Hoe koppel ik Voys aan Salesforce?' → 'Voys Salesforce CRM-koppeling Bubble RedCactus'\n"
    "- 'Ondersteunen jullie Zoom?' → 'Voys Zoom vergader-integratie telefoonkoppeling'\n"
    "- 'Werkt Outlook met Voys?' → 'Voys Outlook e-mailkoppeling agenda-integratie'\n"
    "If NO third-party brand is mentioned, leave the rewrite unchanged "
    "beyond the standard pronoun resolution.\n"
    "3. From the taxonomy below, select ALL node IDs whose topic is genuinely "
    "relevant to the rewritten query. An empty list means no narrowing.\n"
    "{distillation_task}"
    "\nConversation history (oldest → newest):\n{history}\n\n"
    "User's current question: {raw_query}\n\n"
    "Available taxonomy nodes:\n{taxonomy}\n\n"
    "Reply with ONLY a JSON object, no markdown, no explanation:\n"
    '{{"rewritten_query": "<string, max 200 chars, same language as user>", '
    '"taxonomy_node_ids": [<int>, ...]}}'
)

# SPEC-RAG-CORRESPONDENCE-DISTILL-001: classify-prompt counterpart of
# _PASTED_CORRESPONDENCE_DISTILL_BLOCK, phrased as an additional numbered
# task so it composes with tasks 1-3 rather than replacing any of them.
# Empty string when not flagged — byte-identical prompt otherwise.
_PASTED_CORRESPONDENCE_DISTILL_TASK = (
    "4. Pasted correspondence detected: the user's current question "
    "contains pasted third-party correspondence (an email, ticket, or "
    "forwarded thread) with substantial noise. When rewriting (task 1), "
    "distill it into a compact, self-contained search query describing the "
    "core technical or support question or problem — NOT the "
    "correspondence's conclusions or opinions. Preserve reusable domain "
    "terminology verbatim: error codes, protocol/status codes, product and "
    "technology names (e.g. 'SIP 404 Not Found', 'trunk', 'VoIP'). Do NOT "
    "preserve unique per-incident identifiers — Call-IDs, specific "
    "account/trunk/ticket numbers, IP addresses, phone numbers — these "
    "never appear in knowledge-base articles and pulling them into the "
    "search query points it away from the general topic instead of toward "
    "it. Output a short KEYWORD-STYLE phrase (like a search-engine query), "
    "NOT a full grammatical question or sentence — no question words, no "
    "markdown formatting, no punctuation beyond what a code or term itself "
    "requires. Drop mail headers, sender and recipient names and "
    "addresses, dates, greetings, signature blocks, and 'RE:'/'FW:' "
    "subject-chain noise.\n"
)

_TAXONOMY_TREES_TTL_S = 300
_TAXONOMY_COVERAGE_TTL_S = 300
_MAX_KBS_FOR_TAXONOMY = 5


def _retrieve_legacy_headers() -> dict[str, str]:
    if not RETRIEVAL_INTERNAL_SECRET:
        return {}
    return {
        "X-Internal-Secret": RETRIEVAL_INTERNAL_SECRET,
        "X-Caller-Service": "litellm",
    }


def format_history_for_rewrite(history: list[dict], max_chars: int = 1000) -> str:
    """Format the last N turns as plain "ROLE: content" lines, truncated."""
    if not history:
        return "(none)"
    tail = history[-QUERY_REWRITE_HISTORY_TURNS * 2 :]
    lines = []
    used = 0
    for turn in tail:
        role = turn.get("role", "?").upper()
        content = (turn.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        line = f"{role}: {content}"
        if used + len(line) > max_chars:
            line = line[: max_chars - used] + "…"
            lines.append(line)
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if lines else "(none)"


def rewrite_decided(meta: dict) -> bool:
    """Whether the rewrite step made the coreference decision for this query.

    True when the rewrite ran to completion (changed or not) or when the
    destructive-rewrite guard explicitly chose the raw query. False for
    infrastructure skips (disabled, no API key, timeout, empty response):
    retrieval-api may then run its own coreference resolver as fallback.
    Drives the ``coreference_resolved`` field on the /retrieve body — without
    it, a guard-fire (raw_query == query) would re-trigger retrieval-api's
    unguarded rewrite and reopen the incident the guard blocked.
    """
    return meta.get("skipped") in (None, "destructive_rewrite")


def _apply_rewrite_guard(raw_query: str, rewritten: str, meta: dict) -> str:
    if _rewrite_preserves_current_query(raw_query, rewritten):
        return rewritten
    meta["skipped"] = "destructive_rewrite"
    meta["was_changed"] = False
    meta["dropped_salient_tokens"] = sorted(_rewrite_salient_tokens(raw_query))[:8]
    return raw_query


# SPEC-RAG-CORRESPONDENCE-DISTILL-001 HISTORY 0.2.0: live testing against
# production Mistral showed the model does not reliably follow the
# distillation prompt's "no markdown, no long identifiers" instruction — a
# request in English prose is not a guarantee. Enforce both deterministically
# in code, same philosophy as klai_citations.strip_model_citation_artifacts
# elsewhere in this codebase: ask AND verify, never ask alone.
#
# HISTORY 0.4.0 (review finding #7): underscore is NOT stripped here anymore.
# `_` is markdown italic only around whitespace-bounded text (`_word_`); it is
# also the word-separator in reusable technical identifiers like
# ERR_AUTH_FAILED. The original `[*_\`]` pattern mangled those into
# "ERRAUTHFAILED", directly contradicting REQ-2's "preserve error codes
# verbatim". Only `*` (bold/italic) and backtick (inline code) are stripped.
_MARKDOWN_EMPHASIS_RE = re.compile(r"[*`]{1,3}")
# Sol delta-review Fix 2: SIP Call-IDs (token@host, e.g.
# "aa11bb22@203.0.113.42") and email addresses share the same shape and are
# both unique per-incident identifiers that hurt retrieval the same way a
# long digit run does — strip deterministically, same philosophy as the
# digit-run rule below.
_SIP_CALL_ID_RE = re.compile(r"\b[A-Za-z0-9.\-]+@[A-Za-z0-9.\-]+\b")
# Sol delta-review Fix 2: raw IPv4 addresses are unique per-incident network
# identifiers, never reusable domain vocabulary.
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# SIP/HTTP status codes are always exactly 3 digits (1xx-6xx). Any digit run
# of 5+ is almost certainly a unique per-incident identifier (trunk, account,
# ticket, or phone number) rather than reusable domain vocabulary — proven by
# a live A/B on retrieval-api (0.571 top score with such a number present vs.
# 0.847 without, same underlying question).
_LONG_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")
# HISTORY 0.4.0 (review finding #7): a bare digit-run check also strips
# legitimate reusable identifiers — Windows error codes ("error 10060") and
# CVE suffixes ("CVE-2026-12345") are indistinguishable from a phone/trunk
# number by digit-count alone. Protect two structural shapes instead of
# widening the digit threshold (which would just miss shorter incident IDs):
# a run preceded within a few characters by a known code/error keyword
# (error 10060, code 500001, CVE 2026-12345), and a run immediately preceded
# by an UPPERCASE structured-code prefix ending in a hyphen (CVE-2026-12345,
# ERR-10060). Sol delta-review Fix 3 narrowed the hyphen exception from a
# bare `prefix.endswith("-")` (which wrongly preserved lowercase per-incident
# identifiers like "ticket-123456" and "trunk-451030015") to this uppercase
# shape.
_CODE_CONTEXT_RE = re.compile(r"(?i)\b(error|code|status|cve)\b[\s:#-]{0,3}$")
_UPPERCASE_CODE_PREFIX_RE = re.compile(r"[A-Z]{2,}(?:-\d+)*-$")
_CODE_CONTEXT_LOOKBACK_CHARS = 20


def _clean_distilled_query(text: str) -> str:
    """Deterministic cleanup for the pasted-correspondence distillation path.

    Only called for successful distillations (never on the destructive-guard
    fallback, which must return the user's own text untouched) and only when
    ``pasted_correspondence=True`` — ordinary rewrites are never touched.
    """
    cleaned = _MARKDOWN_EMPHASIS_RE.sub("", text)
    cleaned = _SIP_CALL_ID_RE.sub("", cleaned)
    cleaned = _IPV4_RE.sub("", cleaned)

    def _strip_unless_code_context(match: re.Match[str]) -> str:
        prefix = cleaned[
            max(0, match.start() - _CODE_CONTEXT_LOOKBACK_CHARS) : match.start()
        ]
        if _UPPERCASE_CODE_PREFIX_RE.search(prefix) or _CODE_CONTEXT_RE.search(prefix):
            return match.group(0)
        return ""

    cleaned = _LONG_DIGIT_RUN_RE.sub(_strip_unless_code_context, cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _finalize_distilled_rewrite(
    raw_query: str, rewritten: str, meta: dict, *, pasted_correspondence: bool
) -> str:
    """Clean, THEN guard, THEN fall back to raw_query if cleanup emptied it.

    Cleanup must run BEFORE the destructive-rewrite guard: cleaning after the
    guard let a rewrite through that overlapped raw_query ONLY on the exact
    identifier cleanup then strips (e.g. raw "Ticket 123456" vs. model output
    "123456" — the guard accepts the shared token, cleanup then empties it),
    silently sending an empty query to retrieval. Cleaning first means the
    guard's own salient-token-overlap check naturally rejects that case. The
    final empty-string check is a second line of defense for
    ``_rewrite_preserves_current_query``'s vacuous-True branch (raw_query
    itself has no salient tokens), which would otherwise let an empty
    ``rewritten`` slip past the guard unrejected.
    """
    if pasted_correspondence:
        rewritten = _clean_distilled_query(rewritten)
    rewritten = _apply_rewrite_guard(raw_query, rewritten, meta)
    if (
        pasted_correspondence
        and meta.get("skipped") != "destructive_rewrite"
        and not rewritten.strip()
    ):
        meta["skipped"] = "empty_after_distillation"
        rewritten = raw_query
    return rewritten


async def rewrite_query(
    raw_query: str,
    history: list[dict],
    *,
    allow_empty_history: bool = False,
    pasted_correspondence: bool = False,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, dict]:
    """Return ``(rewritten_query, debug_meta)`` — falls back to ``raw_query`` on failure.

    ``pasted_correspondence`` (SPEC-RAG-CORRESPONDENCE-DISTILL-001): when
    True, the SAME LLM call distills pasted third-party correspondence into
    a compact search query instead of forwarding the noisy raw text. Recorded
    in ``meta["pasted_correspondence_detected"]`` on every return path
    (including skips) so telemetry can measure detection rate independent of
    whether the rewrite pipeline actually ran.
    """
    meta: dict = {
        "was_changed": False,
        "rewrite_ms": 0,
        "prompt_variant": "plain",
        "pasted_correspondence_detected": pasted_correspondence,
    }
    if not raw_query or not raw_query.strip():
        meta["skipped"] = "empty_query"
        return raw_query, meta
    if not QUERY_REWRITE_ENABLED:
        meta["skipped"] = "disabled"
        return raw_query, meta
    if not history and not allow_empty_history:
        meta["skipped"] = "no_history"
        return raw_query, meta
    if not MISTRAL_API_KEY:
        meta["skipped"] = "no_api_key"
        return raw_query, meta

    history_str = format_history_for_rewrite(history)
    distillation_block = (
        _PASTED_CORRESPONDENCE_DISTILL_BLOCK if pasted_correspondence else ""
    )
    prompt = _QUERY_REWRITE_PROMPT.format(
        history=history_str,
        raw_query=raw_query,
        distillation_block=distillation_block,
    )
    payload = {
        "model": QUERY_REWRITE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 200,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }

    client_kwargs: dict = {"timeout": QUERY_REWRITE_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport

    t_start = time.monotonic()
    try:
        resp = await _post_to_mistral_throttled(
            payload, headers, client_kwargs, QUERY_REWRITE_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        rewritten_raw = data["choices"][0]["message"]["content"]
    except Exception as exc:
        meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)
        meta["skipped"] = "exception"
        # repr, not str: httpx timeout exceptions stringify to "" which
        # produced unusable `error=` log lines in production.
        meta["error"] = repr(exc)[:120]
        logger.warning(
            "query_rewrite_failed error=%s rewrite_ms=%d",
            meta["error"],
            meta["rewrite_ms"],
        )
        return raw_query, meta

    meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)
    rewritten = (rewritten_raw or "").strip().strip('"').strip("'")
    if not rewritten:
        meta["skipped"] = "empty_response"
        return raw_query, meta
    rewritten = rewritten[:500]
    rewritten = _finalize_distilled_rewrite(
        raw_query, rewritten, meta, pasted_correspondence=pasted_correspondence
    )
    meta["was_changed"] = rewritten.lower() != raw_query.strip().lower()
    return rewritten, meta


def format_taxonomy_for_prompt(
    trees: dict[str, list[dict]] | list[dict],
    max_nodes_per_kb: int = 30,
) -> str:
    """Format taxonomy nodes for the classifier prompt."""
    if isinstance(trees, list):
        if not trees:
            return "(none)"
        lines = [
            f"- id={node['id']}: {node['name']}" for node in trees[:max_nodes_per_kb]
        ]
        if len(trees) > max_nodes_per_kb:
            lines.append(f"... ({len(trees) - max_nodes_per_kb} more nodes omitted)")
        return "\n".join(lines)

    if not trees:
        return "(none)"
    lines: list[str] = []
    for kb_slug in sorted(trees.keys()):
        nodes = trees[kb_slug]
        if not nodes:
            continue
        lines.append(f"[{kb_slug}]")
        for node in nodes[:max_nodes_per_kb]:
            lines.append(f"  - id={node['id']}: {node['name']}")
        if len(nodes) > max_nodes_per_kb:
            lines.append(f"  ... ({len(nodes) - max_nodes_per_kb} more nodes omitted)")
    return "\n".join(lines) if lines else "(none)"


def flatten_trees(trees: dict[str, list[dict]] | list[dict]) -> list[dict]:
    """Return one flat node list across all KBs (for valid_ids lookup)."""
    if isinstance(trees, list):
        return list(trees)
    flat: list[dict] = []
    for nodes in trees.values():
        flat.extend(nodes)
    return flat


def _taxonomy_cache_keys(org_id: str, kb_slugs: list[str]) -> tuple[str, str]:
    """Stable cache keys for trees + coverage, sorted for determinism."""
    sig = ",".join(sorted(set(kb_slugs)))
    return (f"tax_trees:{org_id}:{sig}", f"tax_coverage:{org_id}:{sig}")


async def fetch_taxonomy_trees(
    org_id: str,
    kb_slugs: list[str],
    cache,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, list[dict]]:
    """Fetch trees for all KBs in scope. Returns ``{kb_slug: [node, ...]}``."""
    if not TAXONOMY_ENABLED or not _RETRIEVAL_BASE_URL or not kb_slugs:
        return {}
    if len(kb_slugs) > _MAX_KBS_FOR_TAXONOMY:
        logger.info(
            "KlaiKnowledgeHook: taxonomy_skipped reason=too_many_kbs count=%d",
            len(kb_slugs),
        )
        return {}

    cache_key, _ = _taxonomy_cache_keys(org_id, kb_slugs)
    if cache is not None:
        try:
            cached = await cache.async_get_cache(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return cached

    url = f"{_RETRIEVAL_BASE_URL}/internal/v1/taxonomy/trees"
    legacy_headers = _retrieve_legacy_headers()
    client_kwargs: dict = {"timeout": TAXONOMY_FETCH_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport

    params = [("org_id", org_id)] + [("kb_slugs", s) for s in kb_slugs]
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url, params=params, headers=legacy_headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(
            "KlaiKnowledgeHook: taxonomy_trees_fetch_failed org=%s kbs=%s error=%s",
            org_id,
            kb_slugs,
            str(exc)[:120],
        )
        return {}

    if not isinstance(data, dict):
        return {}

    if cache is not None:
        try:
            await cache.async_set_cache(cache_key, data, ttl=_TAXONOMY_TREES_TTL_S)
        except Exception:
            pass
    return data


async def fetch_taxonomy_coverage(
    org_id: str,
    kb_slugs: list[str],
    cache,
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, float]:
    """Fetch per-KB coverage ratios. Returns ``{kb_slug: 0.0|1.0}``."""
    if not TAXONOMY_ENABLED or not _RETRIEVAL_BASE_URL or not kb_slugs:
        return {slug: 0.0 for slug in kb_slugs}

    _, cache_key = _taxonomy_cache_keys(org_id, kb_slugs)
    if cache is not None:
        try:
            cached = await cache.async_get_cache(cache_key)
        except Exception:
            cached = None
        if isinstance(cached, dict):
            return {slug: float(cached.get(slug, 0.0)) for slug in kb_slugs}

    url = f"{_RETRIEVAL_BASE_URL}/internal/v1/taxonomy/coverage"
    legacy_headers = _retrieve_legacy_headers()
    client_kwargs: dict = {"timeout": TAXONOMY_FETCH_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport
    params = [("org_id", org_id)] + [("kb_slugs", s) for s in kb_slugs]
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url, params=params, headers=legacy_headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(
            "KlaiKnowledgeHook: taxonomy_coverage_fetch_failed org=%s kbs=%s error=%s",
            org_id,
            kb_slugs,
            str(exc)[:120],
        )
        return {slug: 0.0 for slug in kb_slugs}

    coverage = {slug: float(data.get(slug, 0.0)) for slug in kb_slugs}
    if cache is not None:
        try:
            await cache.async_set_cache(
                cache_key, coverage, ttl=_TAXONOMY_COVERAGE_TTL_S
            )
        except Exception:
            pass
    return coverage


async def rewrite_and_classify(
    raw_query: str,
    history: list[dict],
    taxonomy_trees: dict[str, list[dict]] | list[dict],
    *,
    pasted_correspondence: bool = False,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, list[int], dict]:
    """Return ``(rewritten_query, classified_node_ids, debug_meta)`` in one LLM call.

    ``pasted_correspondence``: see :func:`rewrite_query`. Threaded through to
    the plain-rewrite fallback path unchanged, and recorded in
    ``meta["pasted_correspondence_detected"]`` on every return path.
    """
    meta: dict = {
        "was_changed": False,
        "rewrite_ms": 0,
        "prompt_variant": "classify",
        "pasted_correspondence_detected": pasted_correspondence,
    }

    if not raw_query or not raw_query.strip():
        meta["skipped"] = "empty_query"
        return raw_query, [], meta
    if not QUERY_REWRITE_ENABLED:
        meta["skipped"] = "disabled"
        return raw_query, [], meta
    if not MISTRAL_API_KEY:
        meta["skipped"] = "no_api_key"
        return raw_query, [], meta

    flat_tree = flatten_trees(taxonomy_trees)

    if not flat_tree:
        rewritten, rewrite_meta = await rewrite_query(
            raw_query,
            history,
            allow_empty_history=True,
            pasted_correspondence=pasted_correspondence,
            _transport=_transport,
        )
        return rewritten, [], rewrite_meta

    history_str = format_history_for_rewrite(history) if history else "(none)"
    taxonomy_str = format_taxonomy_for_prompt(taxonomy_trees)
    distillation_task = (
        _PASTED_CORRESPONDENCE_DISTILL_TASK if pasted_correspondence else ""
    )
    prompt = _QUERY_REWRITE_AND_CLASSIFY_PROMPT.format(
        history=history_str,
        raw_query=raw_query,
        taxonomy=taxonomy_str,
        distillation_task=distillation_task,
    )
    payload = {
        "model": QUERY_REWRITE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }
    client_kwargs: dict = {"timeout": QUERY_REWRITE_TIMEOUT}
    if _transport is not None:
        client_kwargs["transport"] = _transport

    valid_ids: set[int] = {int(n["id"]) for n in flat_tree}
    t_start = time.monotonic()
    try:
        resp = await _post_to_mistral_throttled(
            payload, headers, client_kwargs, QUERY_REWRITE_TIMEOUT
        )
        resp.raise_for_status()
        raw_content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(raw_content or "{}")
    except Exception as exc:
        meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)
        meta["skipped"] = "exception"
        # repr, not str: httpx timeout exceptions stringify to "".
        meta["error"] = repr(exc)[:120]
        logger.warning(
            "query_rewrite_and_classify_failed error=%s rewrite_ms=%d",
            meta["error"],
            meta["rewrite_ms"],
        )
        return raw_query, [], meta

    meta["rewrite_ms"] = int((time.monotonic() - t_start) * 1000)

    rewritten = (parsed.get("rewritten_query") or "").strip().strip('"').strip("'")
    if not rewritten:
        meta["skipped"] = "empty_rewritten_query"
        return raw_query, [], meta
    rewritten = rewritten[:500]
    rewritten = _finalize_distilled_rewrite(
        raw_query, rewritten, meta, pasted_correspondence=pasted_correspondence
    )
    meta["was_changed"] = rewritten.lower() != raw_query.strip().lower()

    raw_ids = (
        []
        if meta.get("skipped") == "destructive_rewrite"
        else parsed.get("taxonomy_node_ids") or []
    )
    classified: list[int] = []
    for item in raw_ids:
        try:
            node_id = int(item)
        except (TypeError, ValueError):
            continue
        if node_id in valid_ids:
            classified.append(node_id)

    meta["classified_node_ids"] = classified
    return rewritten, classified, meta
