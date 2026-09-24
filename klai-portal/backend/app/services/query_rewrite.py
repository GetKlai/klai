"""Search-query rewrite and taxonomy classification for the internal chat.

Moved as-is from the LiteLLM hook's ``klai_kb_query_rewrite.py`` (one-chat-
pipeline slice 4): one ``klai-fast`` call rewrites the latest question into a
stand-alone search query, classifies it against the KB taxonomy, and distils
pasted correspondence, on every non-trivial internal turn.

Deliberate temporary duplicate: the widget still makes its search query with
``query_paraphrase.py``. Plan step 3 (docs/architecture/chat-quality-history-
and-plan.md §7.3) picks one of the two on the slice-8 replay and deletes the
other; until then each surface keeps the one it was measured with.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx
import structlog
from klai_citations import rewrite_preserves_subject, salient_tokens

from app.core.config import Settings
from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

# Hook defaults (deploy/litellm/klai_kb_query_rewrite.py); production never
# overrode them (deploy/docker-compose.yml sets none of these).
QUERY_REWRITE_TIMEOUT = 1.5
QUERY_REWRITE_MODEL = "klai-fast"
QUERY_REWRITE_HISTORY_TURNS = 4
TAXONOMY_COVERAGE_THRESHOLD = 0.30
TAXONOMY_FETCH_TIMEOUT = 0.8
_TAXONOMY_TTL_S = 300
_MAX_KBS_FOR_TAXONOMY = 5

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
    "Outlook), also include 2\u20134 broader category or related-brand terms in "
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

# SPEC-RAG-CORRESPONDENCE-DISTILL-001: a long, noisy pasted email embedded
# almost verbatim as the retrieval query dilutes the embedding enough that an
# article which DOES answer the question fails to surface. The same rewrite
# call distils it instead; empty when not flagged, so the prompt is otherwise
# byte-identical.
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
    "2\u20134 broader category or related-brand terms in the rewritten query so "
    "the search can find category-specific or partner-brand pages even when "
    "the original brand string is absent from the source content. Stay "
    "within the 200-char limit; same language as the user.\n"
    "Examples:\n"
    "- 'Hoe koppel ik Voys aan Salesforce?' → 'Voys Salesforce CRM-koppeling Bubble RedCactus'\n"
    "- 'Does Outlook work with Voys?' → 'Voys Outlook email integration calendar sync'\n"
    "- 'Unterstützt ihr Zoom?' → 'Voys Zoom Meeting-Integration Telefonkopplung'\n"
    "The rewritten_query MUST be in the language of the user's current "
    "question — the examples above each follow their question's language; "
    "never copy terms from an example written in another language.\n"
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


@dataclass(frozen=True)
class RewriteResult:
    query: str
    # True when the rewrite made the coreference decision (changed or not, or
    # the destructive guard chose the raw query); False on an infrastructure
    # skip, so retrieval-api runs its own coreference as fallback.
    coreference_resolved: bool
    taxonomy_node_ids: list[int]


def _format_history(history: list[dict], max_chars: int = 1000) -> str:
    if not history:
        return "(none)"
    lines = []
    used = 0
    for turn in history[-QUERY_REWRITE_HISTORY_TURNS * 2 :]:
        content = (turn.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        line = f"{turn.get('role', '?').upper()}: {content}"
        if used + len(line) > max_chars:
            lines.append(line[: max_chars - used] + "…")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if lines else "(none)"


def _format_taxonomy(trees: dict[str, list[dict]], max_nodes_per_kb: int = 30) -> str:
    lines: list[str] = []
    for kb_slug in sorted(trees):
        nodes = trees[kb_slug]
        if not nodes:
            continue
        lines.append(f"[{kb_slug}]")
        lines.extend(f"  - id={node['id']}: {node['name']}" for node in nodes[:max_nodes_per_kb])
        if len(nodes) > max_nodes_per_kb:
            lines.append(f"  ... ({len(nodes) - max_nodes_per_kb} more nodes omitted)")
    return "\n".join(lines) if lines else "(none)"


# The model does not reliably follow "no markdown, no long identifiers" in the
# distillation prompt, so both are enforced in code. Underscore is kept: it is
# the separator in reusable codes like ERR_AUTH_FAILED.
_MARKDOWN_EMPHASIS_RE = re.compile(r"[*`]{1,3}")
# SIP Call-IDs and email addresses share the token@host shape; both are
# per-incident identifiers that pull retrieval away from the topic.
_SIP_CALL_ID_RE = re.compile(r"\b[A-Za-z0-9.\-]+@[A-Za-z0-9.\-]+\b")
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# Status codes are 3 digits; a run of 5+ is a trunk, account, ticket or phone
# number (live A/B on retrieval-api: top score 0.571 with one present, 0.847
# without, same question).
_LONG_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")
# ...unless it is a reusable code: "error 10060", "CVE-2026-12345", "ERR-10060".
_CODE_CONTEXT_RE = re.compile(r"(?i)\b(error|code|status|cve)\b[\s:#-]{0,3}$")
_UPPERCASE_CODE_PREFIX_RE = re.compile(r"[A-Z]{2,}(?:-\d+)*-$")
_CODE_CONTEXT_LOOKBACK_CHARS = 20


def _clean_distilled_query(text: str) -> str:
    cleaned = _MARKDOWN_EMPHASIS_RE.sub("", text)
    cleaned = _SIP_CALL_ID_RE.sub("", cleaned)
    cleaned = _IPV4_RE.sub("", cleaned)

    def _strip_unless_code_context(match: re.Match[str]) -> str:
        prefix = cleaned[max(0, match.start() - _CODE_CONTEXT_LOOKBACK_CHARS) : match.start()]
        if _UPPERCASE_CODE_PREFIX_RE.search(prefix) or _CODE_CONTEXT_RE.search(prefix):
            return match.group(0)
        return ""

    cleaned = _LONG_DIGIT_RUN_RE.sub(_strip_unless_code_context, cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _finalize_rewrite(raw_query: str, rewritten: str, meta: dict, *, pasted_correspondence: bool) -> str:
    """Clean, THEN guard, THEN fall back to the raw query if cleanup emptied it.

    Cleaning after the guard let a rewrite through that overlapped the raw
    query only on an identifier cleanup then stripped, sending an empty query.
    """
    if pasted_correspondence:
        rewritten = _clean_distilled_query(rewritten)
    if not rewrite_preserves_subject(raw_query, rewritten):
        # A rewrite that drops the current question's subject is reverted.
        meta["skipped"] = "destructive_rewrite"
        meta["dropped_salient_tokens"] = sorted(salient_tokens(raw_query))[:8]
        return raw_query
    if pasted_correspondence and not rewritten.strip():
        meta["skipped"] = "empty_after_distillation"
        return raw_query
    return rewritten


def _rewrite_decided(meta: dict) -> bool:
    return meta.get("skipped") in (None, "destructive_rewrite")


async def _post_rewrite(settings: Settings, payload: dict) -> str:
    async def _call() -> str:
        async with httpx.AsyncClient(timeout=QUERY_REWRITE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.litellm_base_url}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"] or ""

    return await asyncio.wait_for(_call(), timeout=QUERY_REWRITE_TIMEOUT)


def delegated_org_metadata(zitadel_org_id: str) -> dict:
    # The call runs on the master key, which belongs to no tenant; the PII
    # enforcer accepts the delegated org only from the master key, so the
    # user's question is masked for this org like on the main call.
    return {"_klai_openai_passthrough": True, "_klai_delegated_org_id": zitadel_org_id}


async def _taxonomy(zitadel_org_id: str, kb_slugs: list[str], settings: Settings, kind: str) -> dict:
    """GET /internal/v1/taxonomy/{trees,coverage}, Redis-cached for 5 minutes; {} on any failure."""
    cache_key = f"tax_{kind}:{zitadel_org_id}:{','.join(sorted(set(kb_slugs)))}"
    pool = await get_redis_pool()
    if pool is not None:
        try:
            cached = await pool.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            logger.warning("taxonomy_cache_read_failed", kind=kind, exc_info=True)
    try:
        async with httpx.AsyncClient(timeout=TAXONOMY_FETCH_TIMEOUT) as client:
            resp = await client.get(
                f"{settings.knowledge_retrieve_url}/internal/v1/taxonomy/{kind}",
                params={"org_id": zitadel_org_id, "kb_slugs": kb_slugs},
                headers={
                    "X-Internal-Secret": settings.retrieval_api_internal_secret or settings.internal_secret,
                    "X-Caller-Service": "portal-api",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("taxonomy_fetch_failed", kind=kind, kb_count=len(kb_slugs), exc_info=True)
        return {}
    if not isinstance(data, dict):
        return {}
    if pool is not None:
        try:
            await pool.set(cache_key, json.dumps(data), ex=_TAXONOMY_TTL_S)
        except Exception:
            logger.warning("taxonomy_cache_write_failed", kind=kind, exc_info=True)
    return data


async def _classified_trees(zitadel_org_id: str, kb_slugs: list[str], settings: Settings) -> dict[str, list[dict]]:
    """Trees of the in-scope KBs whose curated coverage meets the threshold.

    Only an explicit KB set can be classified: "every org KB" and personal-only
    scope have no slug list to fetch trees for, and retrieval-api still applies
    its own scope filters there.
    """
    if not kb_slugs or not settings.knowledge_retrieve_url or len(kb_slugs) > _MAX_KBS_FOR_TAXONOMY:
        return {}
    trees, coverage = await asyncio.gather(
        _taxonomy(zitadel_org_id, kb_slugs, settings, "trees"),
        _taxonomy(zitadel_org_id, kb_slugs, settings, "coverage"),
    )
    return {
        slug: trees.get(slug) or []
        for slug in kb_slugs
        if float(coverage.get(slug, 0.0)) >= TAXONOMY_COVERAGE_THRESHOLD
    }


async def rewrite_for_retrieval(
    query: str,
    history: list[dict],
    *,
    zitadel_org_id: str,
    kb_slugs: list[str],
    pasted_correspondence: bool,
    settings: Settings,
) -> RewriteResult:
    """Rewrite + classify in one call; the raw query on any failure (fail-open)."""
    trees = await _classified_trees(zitadel_org_id, kb_slugs, settings)
    flat_tree = [node for nodes in trees.values() for node in nodes]
    history_str = _format_history(history)
    if flat_tree:
        prompt_variant = "classify"
        prompt = _QUERY_REWRITE_AND_CLASSIFY_PROMPT.format(
            history=history_str,
            raw_query=query,
            taxonomy=_format_taxonomy(trees),
            distillation_task=_PASTED_CORRESPONDENCE_DISTILL_TASK if pasted_correspondence else "",
        )
        payload = {"max_tokens": 300, "response_format": {"type": "json_object"}}
    else:
        prompt_variant = "plain"
        prompt = _QUERY_REWRITE_PROMPT.format(
            history=history_str,
            raw_query=query,
            distillation_block=_PASTED_CORRESPONDENCE_DISTILL_BLOCK if pasted_correspondence else "",
        )
        payload = {"max_tokens": 200}
    payload |= {
        "model": QUERY_REWRITE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "metadata": delegated_org_metadata(zitadel_org_id),
    }

    meta: dict = {}
    started = time.monotonic()
    rewritten = query
    node_ids: list[int] = []
    try:
        content = await _post_rewrite(settings, payload)
        if flat_tree:
            parsed = json.loads(content or "{}")
            content = parsed.get("rewritten_query") or ""
            raw_ids = parsed.get("taxonomy_node_ids") or []
        else:
            raw_ids = []
        candidate = content.strip().strip('"').strip("'")[:500]
        if not candidate:
            meta["skipped"] = "empty_response"
        else:
            rewritten = _finalize_rewrite(query, candidate, meta, pasted_correspondence=pasted_correspondence)
            if meta.get("skipped") != "destructive_rewrite":
                valid_ids = {int(node["id"]) for node in flat_tree}
                for item in raw_ids:
                    try:
                        node_id = int(item)
                    except (TypeError, ValueError):
                        continue
                    if node_id in valid_ids:
                        node_ids.append(node_id)
    except Exception as exc:
        meta["skipped"] = "exception"
        meta["error"] = repr(exc)[:120]

    # Warning level: this is decision telemetry that must reach VictoriaLogs.
    # Query text is never logged here (SPEC-PRIVACY-QUERY-SHADOW-001 REQ-6).
    logger.warning(
        "query_rewrite_metadata",
        rewrite_ms=int((time.monotonic() - started) * 1000),
        was_changed=rewritten.lower() != query.strip().lower(),
        skipped=meta.get("skipped", ""),
        error=meta.get("error"),
        prompt_variant=prompt_variant,
        pasted_correspondence_detected=pasted_correspondence,
        dropped_token_count=len(meta.get("dropped_salient_tokens", [])),
        classified_node_ids=node_ids,
    )
    return RewriteResult(query=rewritten, coreference_resolved=_rewrite_decided(meta), taxonomy_node_ids=node_ids)
