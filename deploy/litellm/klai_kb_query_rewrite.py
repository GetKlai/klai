"""Query rewrite and taxonomy classification helpers for LiteLLM KB retrieval.

Environment-derived constants in this module are boot-time configuration:
production imports the LiteLLM hook once per process, and runtime env toggles
take effect on process restart.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

import httpx

logger = logging.getLogger("klai_knowledge")

QUERY_REWRITE_ENABLED = os.getenv("QUERY_REWRITE_ENABLED", "true").lower() == "true"
QUERY_REWRITE_TIMEOUT = float(os.getenv("QUERY_REWRITE_TIMEOUT", "1.5"))
QUERY_REWRITE_MODEL = os.getenv("QUERY_REWRITE_MODEL", "mistral-small-2603")
QUERY_REWRITE_HISTORY_TURNS = int(os.getenv("QUERY_REWRITE_HISTORY_TURNS", "4"))
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

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
    "Conversation history (oldest → newest):\n{history}\n\n"
    "User's current question: {raw_query}\n\n"
    "Reply with ONLY the rewritten question, no preamble, no explanation, "
    "no quotes. Maximum 200 characters. Same language as the user's input."
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
    "relevant to the rewritten query. An empty list means no narrowing.\n\n"
    "Conversation history (oldest → newest):\n{history}\n\n"
    "User's current question: {raw_query}\n\n"
    "Available taxonomy nodes:\n{taxonomy}\n\n"
    "Reply with ONLY a JSON object, no markdown, no explanation:\n"
    '{{"rewritten_query": "<string, max 200 chars, same language as user>", '
    '"taxonomy_node_ids": [<int>, ...]}}'
)

_TAXONOMY_TREES_TTL_S = 300
_TAXONOMY_COVERAGE_TTL_S = 300
_MAX_KBS_FOR_TAXONOMY = 5
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_DEICTIC_REWRITE_TOKENS = {
    "aanvraag",
    "daar",
    "daarover",
    "dat",
    "deze",
    "die",
    "dit",
    "doe",
    "er",
    "hij",
    "hem",
    "hier",
    "hierover",
    "his",
    "it",
    "its",
    "je",
    "that",
    "them",
    "these",
    "they",
    "this",
    "those",
    "what",
    "welke",
    "wie",
    "zij",
}
_QUERY_REWRITE_STOPWORDS = _DEICTIC_REWRITE_TOKENS | {
    "about",
    "also",
    "and",
    "anything",
    "are",
    "can",
    "could",
    "een",
    "eens",
    "for",
    "from",
    "gaat",
    "give",
    "heb",
    "hebt",
    "heeft",
    "hoe",
    "iets",
    "jij",
    "jullie",
    "kan",
    "kun",
    "kunt",
    "me",
    "meer",
    "met",
    "mij",
    "naar",
    "over",
    "please",
    "status",
    "tell",
    "the",
    "van",
    "voor",
    "wat",
    "weet",
    "with",
    "you",
}


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


def _rewrite_salient_tokens(text: str) -> set[str]:
    return {
        token
        for token in (match.group(0).lower() for match in _TOKEN_RE.finditer(text))
        if len(token) >= 3 and token not in _QUERY_REWRITE_STOPWORDS
    }


def _is_followup_query(text: str) -> bool:
    tokens = {
        match.group(0).lower() for match in _TOKEN_RE.finditer(text) if match.group(0)
    }
    return bool(tokens & _DEICTIC_REWRITE_TOKENS) and not (
        tokens - _QUERY_REWRITE_STOPWORDS
    )


def _rewrite_preserves_current_query(raw_query: str, rewritten: str) -> bool:
    """Reject rewrites that drop the current query's explicit subject.

    LLM rewrite may resolve short follow-ups from history, but a clear current
    question must keep at least one salient token from that question. This
    fails closed for the observed incident class: "Wat weet je over klai?" was
    rewritten to an unrelated Yealink/IP-telefonie query and then retrieved low
    confidence chunks.
    """
    if _is_followup_query(raw_query):
        return True
    raw_tokens = _rewrite_salient_tokens(raw_query)
    if not raw_tokens:
        return True
    rewritten_tokens = _rewrite_salient_tokens(rewritten)
    return bool(raw_tokens & rewritten_tokens)


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


async def rewrite_query(
    raw_query: str,
    history: list[dict],
    *,
    allow_empty_history: bool = False,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, dict]:
    """Return ``(rewritten_query, debug_meta)`` — falls back to ``raw_query`` on failure."""
    meta: dict = {"was_changed": False, "rewrite_ms": 0, "prompt_variant": "plain"}
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
    prompt = _QUERY_REWRITE_PROMPT.format(history=history_str, raw_query=raw_query)
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
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(MISTRAL_API_URL, json=payload, headers=headers)
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
    rewritten = _apply_rewrite_guard(raw_query, rewritten, meta)
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
    _transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, list[int], dict]:
    """Return ``(rewritten_query, classified_node_ids, debug_meta)`` in one LLM call."""
    meta: dict = {"was_changed": False, "rewrite_ms": 0, "prompt_variant": "classify"}

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
            _transport=_transport,
        )
        return rewritten, [], rewrite_meta

    history_str = format_history_for_rewrite(history) if history else "(none)"
    taxonomy_str = format_taxonomy_for_prompt(taxonomy_trees)
    prompt = _QUERY_REWRITE_AND_CLASSIFY_PROMPT.format(
        history=history_str,
        raw_query=raw_query,
        taxonomy=taxonomy_str,
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
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(MISTRAL_API_URL, json=payload, headers=headers)
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
    rewritten = _apply_rewrite_guard(raw_query, rewritten, meta)
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
