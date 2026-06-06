"""Query rewrite and taxonomy classification helpers for LiteLLM KB retrieval.

Environment-derived constants in this module are boot-time configuration:
production imports the LiteLLM hook once per process, and runtime env toggles
take effect on process restart.
"""

from __future__ import annotations

import json
import logging
import os
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
    "If already clear, return it unchanged.\n"
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


async def rewrite_query(
    raw_query: str,
    history: list[dict],
    *,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, dict]:
    """Return ``(rewritten_query, debug_meta)`` — falls back to ``raw_query`` on failure."""
    meta: dict = {"was_changed": False, "rewrite_ms": 0}
    if not raw_query or not raw_query.strip():
        meta["skipped"] = "empty_query"
        return raw_query, meta
    if not QUERY_REWRITE_ENABLED:
        meta["skipped"] = "disabled"
        return raw_query, meta
    if not history:
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
        meta["error"] = str(exc)[:120]
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
    meta: dict = {"was_changed": False, "rewrite_ms": 0}

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

    if not history and not flat_tree:
        meta["skipped"] = "no_history_no_tree"
        return raw_query, [], meta

    if not flat_tree:
        rewritten, rewrite_meta = await rewrite_query(
            raw_query, history, _transport=_transport
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
        meta["error"] = str(exc)[:120]
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
    meta["was_changed"] = rewritten.lower() != raw_query.strip().lower()

    raw_ids = parsed.get("taxonomy_node_ids") or []
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
