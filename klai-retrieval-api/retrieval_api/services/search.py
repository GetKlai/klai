"""Qdrant search on klai_knowledge.

klai_knowledge uses named vectors (vector_chunk, vector_questions, vector_sparse)
with 3-leg RRF fusion. Falls back to 2-leg RRF when sparse vector is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import warnings
from collections import Counter
from datetime import UTC, datetime, timedelta

import structlog
from klai_chat_prompts.language import identify_surface_language
from klai_kb_slugs import personal_kb_slug
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Condition,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
    SparseVector,
)

from retrieval_api.config import settings
from retrieval_api.models import RetrieveRequest
from retrieval_api.services import coreference
from retrieval_api.services.events import get_pool
from retrieval_api.services.tei import embed_single, embed_sparse
from retrieval_api.util.payload import payload_list

# Qdrant client warns at call-time when an api_key is used over plain HTTP;
# safe inside the Docker network, so silence that specific warning.
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

logger = structlog.get_logger()

_client: AsyncQdrantClient | None = None
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ACTIVE_UNTIL_SENTINEL = 253402300800


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=5.0,
        )
    return _client


def _epoch_seconds_to_iso(value: object, *, open_ended_sentinel: bool = False) -> str | None:
    """Convert epoch-second temporal payload values to API ISO strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if open_ended_sentinel and value >= _ACTIVE_UNTIL_SENTINEL:
            return None
        try:
            return (_EPOCH + timedelta(seconds=float(value))).isoformat()
        except (OverflowError, ValueError):
            return None
    return None


def _payload_valid_at(payload: dict) -> str | None:
    return payload.get("valid_at") or _epoch_seconds_to_iso(payload.get("valid_from"))


def _payload_invalid_at(payload: dict) -> str | None:
    return payload.get("invalid_at") or _epoch_seconds_to_iso(
        payload.get("valid_until"),
        open_ended_sentinel=True,
    )


def _temporal_validity_filter() -> Filter:
    """Exclude chunks outside either supported temporal payload contract.

    Retrieval historically used ``valid_at``/``invalid_at`` while
    knowledge-ingest writes ``valid_from``/``valid_until`` from
    ``belief_time_start``/``belief_time_end``. Support both so stale points are
    filtered even when old Qdrant payloads were not physically deleted.
    """
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    now_epoch = int(now.timestamp())
    return Filter(
        must_not=[
            FieldCondition(
                key="invalid_at",
                range={"lte": now_iso},
            ),
            FieldCondition(
                key="valid_until",
                range={"lte": now_epoch},
            ),
            FieldCondition(
                key="valid_at",
                range={"gt": now_iso},
            ),
            FieldCondition(
                key="valid_from",
                range={"gt": now_epoch},
            ),
        ]
    )


# Graphiti returns at most 10 edges per query, so this bound is well above
# the real fan-out; it exists so a pathological result set cannot turn
# label resolution into an unbounded burst of Qdrant reads.
_ARTIFACT_METADATA_MAX = 20
_ARTIFACT_METADATA_TIMEOUT = 2.0


def _scope_filter(request: RetrieveRequest) -> list[FieldCondition | Filter]:
    """Build scope-specific Qdrant filter conditions for klai_knowledge.

    Visibility enforcement: chunks with visibility='private' are excluded from org/both
    scopes unless the requesting user owns them (matched via user_id).
    """
    conditions: list[FieldCondition | Filter] = [
        FieldCondition(key="org_id", match=MatchValue(value=request.org_id)),
    ]
    if request.scope == "personal":
        if request.user_id:
            # SPEC-RAG-PERSONAL-SCOPE-001 REQ-2: scope=personal narrows to
            # the requester's canonical Persoonlijk-KB slug. The slug
            # template ``personal-<user_id>`` is the structural ownership
            # proof — a chunk with this slug is BY CONSTRUCTION part of
            # the canonical Persoonlijk-KB of the user encoded in the
            # suffix, regardless of whether the chunk's ``user_id``
            # payload was stamped at ingest.
            #
            # PR #709 added an OR-filter (user_id OR canonical_slug) to
            # recover from connectors that did not stamp user_id.
            # However, the user_id-OR branch let chunks from non-canonical
            # user-owned KBs (e.g. ``test2``) leak through whenever the
            # caller was the chunk's owner — exactly the Jantine bug we
            # want to close. The canonical-slug branch alone matches both
            # cases the OR was designed to handle:
            #   - Modern chunks (kb_slug=canonical, user_id=me): pass
            #   - Legacy chunks (kb_slug=canonical, user_id=None): pass
            # and rejects the leak case:
            #   - Other user-owned KB chunks (kb_slug=test2, user_id=me): blocked
            conditions.append(
                FieldCondition(
                    key="kb_slug",
                    match=MatchValue(value=personal_kb_slug(request.user_id)),
                )
            )
        # personal scope is already restricted to one user; no visibility filter needed
    else:
        # org / both: exclude private chunks that do not belong to the requesting user
        not_private = Filter(
            must_not=[FieldCondition(key="visibility", match=MatchValue(value="private"))]
        )
        visibility_should: list[Filter] = [not_private]
        if request.user_id:
            own_private_must: list[FieldCondition] = [
                FieldCondition(key="visibility", match=MatchValue(value="private")),
                FieldCondition(key="user_id", match=MatchValue(value=request.user_id)),
            ]
            # SPEC-RAG-PERSONAL-SCOPE-001 REQ-3: scope=both normally must not
            # use `user_id` as an unconstrained private-KB bypass. It may include
            # the canonical Persoonlijk KB and any caller-selected KB slugs, but
            # selecting only Persoonlijk must not leak every private chunk owned
            # by the user.
            #
            # The one deliberate exception is the explicit "all collections"
            # contract: include_owned_private_kbs=True means kb_slugs=None still
            # covers all org KBs while also allowing all caller-owned private
            # KBs. Ownership remains enforced by user_id, so this cannot expose
            # another user's private chunks.
            # scope=org keeps the wider semantic because partner_chat does not
            # model a Persoonlijk-vs-other-user-KB dropdown today.
            if request.scope == "both" and not request.include_owned_private_kbs:
                allowed_private_slugs = list(
                    dict.fromkeys(
                        [
                            personal_kb_slug(request.user_id),
                            *(request.kb_slugs or []),
                        ]
                    )
                )
                own_private_must.append(
                    FieldCondition(
                        key="kb_slug",
                        match=MatchAny(any=allowed_private_slugs),
                    )
                )
            visibility_should.append(Filter(must=own_private_must))
        conditions.append(Filter(should=visibility_should))
    if request.kb_slugs:
        if request.scope == "both" and request.user_id:
            # kb_slugs is an org-only filter. When scope=both, personal chunks must not
            # be excluded by the slug filter — a chunk passes if it matches a selected
            # slug OR lives in the requester's canonical Persoonlijk KB. The bypass is
            # slug-based, not user_id-based: a user_id match would also pass
            # user-stamped chunks in NON-selected org KBs, silently widening the
            # selected-KB semantics (Codex review 2026-06-11). The canonical slug also
            # covers personal chunks whose user_id payload was never stamped at ingest.
            conditions.append(
                Filter(
                    should=[
                        FieldCondition(key="kb_slug", match=MatchAny(any=request.kb_slugs)),
                        FieldCondition(
                            key="kb_slug",
                            match=MatchValue(value=personal_kb_slug(request.user_id)),
                        ),
                    ]
                )
            )
        else:
            conditions.append(FieldCondition(key="kb_slug", match=MatchAny(any=request.kb_slugs)))
    return conditions


# The languages of the searched KBs are those of at least a quarter of a sample
# of their chunks, identified the way the query is. A selection can hold more
# than one: measured on 23 October 2026, one org's KBs together were 1028 Dutch
# and 860 English chunks out of 2000, and a sample of 32 split 14 to 15, so a
# single "dominant" language flipped with the sample. 128 chunks take about
# 80 ms to identify. Cached for an hour per org and KB selection: a stale
# answer only costs or saves one translated leg.
_KB_LANGUAGE_SAMPLE = 128
_KB_LANGUAGE_MIN_SHARE = 0.25
_KB_LANGUAGE_TTL_SECONDS = 3600.0
_KB_LANGUAGE_TIMEOUT = 2.0
_kb_language_cache: dict[tuple[str, tuple[str, ...]], tuple[float, list[str]]] = {}

# Aliases are a KB setting (portal_knowledge_bases.aliases) the KB owner edits;
# a minute of staleness is the price of not reading Postgres on every query.
_ALIAS_TTL_SECONDS = 60.0
_alias_cache: dict[tuple[str, tuple[str, ...]], tuple[float, list[list[str]]]] = {}
_ALIAS_SQL = """
    SELECT kb.aliases
    FROM portal_knowledge_bases kb
    JOIN portal_orgs org ON org.id = kb.org_id
    WHERE org.zitadel_org_id = $1
      AND (cardinality($2::text[]) = 0 OR kb.slug = ANY($2::text[]))
"""


def _kb_selection(request: RetrieveRequest) -> tuple[str, ...]:
    """The KB slugs a request searches; empty means every KB of the org."""
    if request.kb_slugs:
        return tuple(sorted(request.kb_slugs))
    if request.scope == "personal" and request.user_id:
        return (personal_kb_slug(request.user_id),)
    return ()


async def _kb_languages(request: RetrieveRequest, slugs: tuple[str, ...]) -> list[str]:
    """The languages of the searched KBs; empty when unknown.

    The sample is read without the visibility filter: only language codes
    leave this function, never text, and they are shared by every caller of
    the same KB selection. A failed read is not cached.
    """
    key = (request.org_id, slugs)
    cached = _kb_language_cache.get(key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    conditions: list[Condition] = [
        FieldCondition(key="org_id", match=MatchValue(value=request.org_id))
    ]
    if slugs:
        conditions.append(FieldCondition(key="kb_slug", match=MatchAny(any=list(slugs))))
    try:
        points, _ = await asyncio.wait_for(
            _get_client().scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=Filter(must=conditions),
                limit=_KB_LANGUAGE_SAMPLE,
                with_payload=["text"],
                with_vectors=False,
            ),
            timeout=_KB_LANGUAGE_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("kb_language_lookup_failed", error=type(exc).__name__)
        return []
    texts = [(point.payload or {}).get("text") or "" for point in points]
    votes = Counter(await asyncio.to_thread(lambda: [identify_surface_language(t) for t in texts]))
    languages = [
        language
        for language, count in votes.most_common()
        if language and count >= _KB_LANGUAGE_MIN_SHARE * len(texts)
    ]
    _kb_language_cache[key] = (time.monotonic() + _KB_LANGUAGE_TTL_SECONDS, languages)
    return languages


async def _alias_groups(request: RetrieveRequest, slugs: tuple[str, ...]) -> list[list[str]]:
    """Every alias group of the searched KBs; empty on any lookup failure."""
    key = (request.org_id, slugs)
    cached = _alias_cache.get(key)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    pool = get_pool()
    if pool is None:
        return []
    try:
        rows = await pool.fetch(_ALIAS_SQL, request.org_id, list(slugs))
    except Exception:
        logger.warning("kb_alias_lookup_failed", exc_info=True)
        return []
    # asyncpg hands jsonb over as text: the pool registers no JSON codec.
    groups = [group for row in rows for group in json.loads(row["aliases"])]
    _alias_cache[key] = (time.monotonic() + _ALIAS_TTL_SECONDS, groups)
    return groups


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def expand_with_aliases(query: str, groups: list[list[str]]) -> str | None:
    """*query* plus the other names of every alias group it mentions, or None."""
    extra: list[str] = []
    for group in groups:
        if any(_mentions(query, term) for term in group):
            extra += [t for t in group if t not in extra and not _mentions(query, t)]
    return f"{query} {' '.join(extra)}" if extra else None


async def _extra_query_texts(query: str, request: RetrieveRequest) -> list[str]:
    """The texts of the extra legs: the translated and the alias-expanded queries.

    One translation per KB language other than the query's, and only when the
    query's language is known; the expansion only when an alias matched.
    Logged as language codes and counts, never as text.
    """
    slugs = _kb_selection(request)
    query_language = identify_surface_language(query)
    kb_languages, groups = await asyncio.gather(
        _kb_languages(request, slugs) if query_language else asyncio.sleep(0, []),
        _alias_groups(request, slugs),
    )
    targets = [language for language in kb_languages if language != query_language]
    translations = await asyncio.gather(*(coreference.translate(query, t) for t in targets))
    expanded = expand_with_aliases(query, groups)
    logger.info(
        "retrieval_extra_legs",
        query_language=query_language,
        kb_languages=",".join(kb_languages),
        translated_legs=sum(1 for t in translations if t),
        translation_failures=sum(1 for t in translations if not t),
        alias_leg=expanded is not None,
    )
    return [text for text in (*translations, expanded) if text]


async def _search_knowledge(
    query_vector: list[float],
    request: RetrieveRequest,
    candidates: int,
    sparse_vector: SparseVector | None = None,
    raw_query_vector: list[float] | None = None,
    raw_sparse_vector: SparseVector | None = None,
    extra_legs: list[tuple[list[float], SparseVector | None]] | None = None,
) -> list[dict]:
    """3-leg RRF hybrid search on klai_knowledge (named vectors).

    Prefetch legs: vector_chunk + vector_questions (always) + vector_sparse (when available).
    Falls back to 2-leg RRF when sparse_vector is None.

    When ``raw_query_vector`` is supplied (the embedding of the user's
    pre-rewrite ``raw_query``), two extra prefetch legs are fused in: a dense
    ``vector_chunk`` leg and, when available, a ``vector_sparse`` leg on the raw
    query. This guarantees that a literal-term match (e.g. a product name) still
    enters the candidate pool even when an over-eager coreference/query rewrite
    steered the resolved query away from it. The reranker (which already scores
    against ``raw_query`` + resolved) then promotes the genuinely relevant hit.
    Pass ``raw_query_vector`` only when the rewrite changed the query, so the
    no-rewrite path keeps its 2/3-leg shape and adds no extra Qdrant work.

    ``extra_legs`` (the translated and the alias-expanded query) add a dense
    ``vector_chunk`` and a ``vector_sparse`` leg each, the same shape.
    """
    client = _get_client()

    scope_conditions = _scope_filter(request)
    must_conditions = [*scope_conditions, _temporal_validity_filter()]

    # SPEC-KB-022 R3: taxonomy filter with backward-compatible fallback.
    # OR: match on new taxonomy_node_ids (array) OR old taxonomy_node_id (int).
    if request.taxonomy_node_ids:
        taxonomy_should = [
            FieldCondition(
                key="taxonomy_node_ids",
                match=MatchAny(any=request.taxonomy_node_ids),
            ),
            FieldCondition(
                key="taxonomy_node_id",
                match=MatchAny(any=request.taxonomy_node_ids),
            ),
        ]
        must_conditions.append(Filter(should=taxonomy_should))

    # SPEC-KB-022 R3: optional tag filter
    if request.tags:
        must_conditions.append(
            FieldCondition(
                key="tags",
                match=MatchAny(any=request.tags),
            )
        )

    query_filter = Filter(must=must_conditions)

    prefetch_limit = max(candidates * 4, 20)
    prefetch = [
        Prefetch(
            query=query_vector,
            using="vector_chunk",
            limit=prefetch_limit,
            filter=query_filter,
        ),
        Prefetch(
            query=query_vector,
            using="vector_questions",
            limit=prefetch_limit,
            filter=query_filter,
        ),
    ]
    if sparse_vector is not None:
        prefetch.append(
            Prefetch(
                query=sparse_vector,
                using="vector_sparse",
                limit=prefetch_limit,
                filter=query_filter,
            )
        )
    legs = list(extra_legs or [])
    if raw_query_vector is not None:
        legs.insert(0, (raw_query_vector, raw_sparse_vector))
    for dense, sparse in legs:
        prefetch.append(
            Prefetch(query=dense, using="vector_chunk", limit=prefetch_limit, filter=query_filter)
        )
        if sparse is not None:
            prefetch.append(
                Prefetch(
                    query=sparse, using="vector_sparse", limit=prefetch_limit, filter=query_filter
                )
            )

    try:
        result = await asyncio.wait_for(
            client.query_points(
                collection_name=settings.qdrant_collection,
                prefetch=prefetch,
                query=FusionQuery(fusion=Fusion.RRF),
                limit=candidates,
                with_payload=True,
            ),
            timeout=5.0,
        )
    except Exception:
        # SPEC-SEC-HYGIENE-001 REQ-43: logger.exception preserves the
        # traceback (TRY400/TRY401); the previous `error=str(exc)` discarded it.
        logger.exception("qdrant_search_failed", collection=settings.qdrant_collection)
        raise

    return [
        {
            "chunk_id": str(r.id),
            "text": r.payload.get("text", ""),
            "score": r.score,
            "artifact_id": r.payload.get("artifact_id"),
            "content_type": r.payload.get("content_type"),
            "context_prefix": r.payload.get("context_prefix"),
            "heading_path": r.payload.get("heading_path"),
            "parent_chunk_id": r.payload.get("parent_chunk_id"),
            "scope": r.payload.get("scope"),
            "valid_at": _payload_valid_at(r.payload),
            "invalid_at": _payload_invalid_at(r.payload),
            "ingested_at": r.payload.get("ingested_at"),
            "assertion_mode": r.payload.get("assertion_mode"),
            "entity_pagerank_max": r.payload.get("entity_pagerank_max"),
            "entity_names": payload_list(r.payload, "entity_names"),
            "source_url": r.payload.get("source_url"),
            "source_ref": r.payload.get("source_ref"),
            "source_connector_id": r.payload.get("source_connector_id"),
            "kb_slug": r.payload.get("kb_slug"),
            "source_label": r.payload.get("source_label"),
            "title": r.payload.get("title"),
            "original_filename": r.payload.get("original_filename"),
            "image_urls": payload_list(r.payload, "image_urls"),
        }
        for r in result.points
    ]


async def _fetch_one_artifact_metadata(
    artifact_id: str,
    request: RetrieveRequest,
) -> tuple[str, dict[str, str | None]] | None:
    """Fetch the display fields of ONE artifact. Returns None when unresolved."""
    client = _get_client()
    artifact_filter = Filter(
        must=[
            *_scope_filter(request),
            FieldCondition(key="artifact_id", match=MatchValue(value=artifact_id)),
            _temporal_validity_filter(),
        ]
    )
    try:
        result, _ = await asyncio.wait_for(
            client.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=artifact_filter,
                limit=1,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=_ARTIFACT_METADATA_TIMEOUT,
        )
    except Exception:
        logger.warning("artifact_display_metadata_failed", artifact_id=artifact_id, exc_info=True)
        return None
    if not result:
        return None
    payload = result[0].payload
    return artifact_id, {
        "title": payload.get("title"),
        "source_url": payload.get("source_url"),
        "source_label": payload.get("source_label"),
        "original_filename": payload.get("original_filename"),
    }


async def _fetch_one_document_metadata(
    kb_slug: str,
    path: str,
    request: RetrieveRequest,
) -> tuple[tuple[str, str], dict[str, str | None]] | None:
    """Fetch display fields for ONE document, keyed by (kb_slug, path)."""
    client = _get_client()
    document_filter = Filter(
        must=[
            *_scope_filter(request),
            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
            FieldCondition(key="path", match=MatchValue(value=path)),
            _temporal_validity_filter(),
        ]
    )
    try:
        result, _ = await asyncio.wait_for(
            client.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=document_filter,
                limit=1,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=_ARTIFACT_METADATA_TIMEOUT,
        )
    except Exception:
        logger.warning("document_display_metadata_failed", kb_slug=kb_slug, exc_info=True)
        return None
    if not result:
        return None
    payload = result[0].payload
    return (kb_slug, path), {
        "title": payload.get("title"),
        "source_url": payload.get("source_url"),
        "source_label": payload.get("source_label"),
        "original_filename": payload.get("original_filename"),
        # The CURRENT version's id. A graph edge outlives the artifact it was
        # extracted from, so this is the only id worth putting on a citation.
        "artifact_id": payload.get("artifact_id"),
    }


async def fetch_document_display_metadata(
    documents: list[tuple[str, str]],
    request: RetrieveRequest,
) -> dict[tuple[str, str], dict[str, str | None]]:
    """Resolve (kb_slug, path) -> the fields a citation is rendered from.

    This is the resolution path for episodes named by SPEC-RAG-GRAPH-CITE-002.
    Unlike artifact_id it survives re-ingest, because ingest mints a new
    artifact uuid every time while ``(org_id, kb_slug, path)`` is the identity
    it dedups on.

    Same shape as ``fetch_artifact_display_metadata``: tenant-scoped through
    ``_scope_filter``, one bounded lookup per document, fail-open per entry.
    """
    unique = sorted({(kb, p) for kb, p in documents if kb and p})
    if not unique:
        return {}
    if len(unique) > _ARTIFACT_METADATA_MAX:
        logger.warning(
            "document_display_metadata_truncated",
            requested=len(unique),
            resolved=_ARTIFACT_METADATA_MAX,
        )
        unique = unique[:_ARTIFACT_METADATA_MAX]

    resolved = await asyncio.gather(
        *(_fetch_one_document_metadata(kb, p, request) for kb, p in unique)
    )
    return {key: meta for entry in resolved if entry for key, meta in [entry]}


async def fetch_artifact_display_metadata(
    artifact_ids: list[str],
    request: RetrieveRequest,
) -> dict[str, dict[str, str | None]]:
    """Resolve artifact_id -> the fields a citation is rendered from.

    Graph edges carry a fact and (since SPEC-RAG-GRAPH-CITE-001) the
    artifact_id they were extracted from, but no title or URL. Without those
    ``evidence_pack._title()`` falls through to ``text[:80]`` and the source
    list shows a truncated fact instead of the document — which defeats the
    point of citing it at all.

    One bounded lookup PER artifact rather than a single wider scroll. A
    scroll is not grouped, so one large document can fill the whole page and
    starve a one-chunk document of its label — the very defect this function
    exists to fix, reappearing for any mix of document sizes. Per-artifact
    lookups make that impossible instead of unlikely; they are limit=1 reads
    on an indexed field and run concurrently.

    Scoped through ``_scope_filter`` like every other read here. That is not
    ceremony: a title alone can be sensitive, so the visibility rules that
    keep private chunks out of a scope must also keep their titles out of a
    source list.

    Fail-open: an unresolved artifact costs a label, never the graph result.
    """
    unique_ids = sorted({aid for aid in artifact_ids if aid})
    if not unique_ids:
        return {}

    if len(unique_ids) > _ARTIFACT_METADATA_MAX:
        # No silent caps: say what was dropped rather than let the tail
        # quietly render as truncated facts.
        logger.warning(
            "artifact_display_metadata_truncated",
            requested=len(unique_ids),
            resolved=_ARTIFACT_METADATA_MAX,
        )
        unique_ids = unique_ids[:_ARTIFACT_METADATA_MAX]

    resolved = await asyncio.gather(
        *(_fetch_one_artifact_metadata(aid, request) for aid in unique_ids)
    )
    return {aid: meta for entry in resolved if entry for aid, meta in [entry]}


async def hybrid_search(
    query_vector: list[float],
    request: RetrieveRequest,
    candidates: int,
    sparse_vector: SparseVector | None = None,
    raw_query_vector: list[float] | None = None,
    raw_sparse_vector: SparseVector | None = None,
    query_text: str | None = None,
) -> list[dict]:
    """Run Qdrant search appropriate for the request scope.

    Returns raw result dicts with text, score, and payload fields.
    sparse_vector is forwarded to _search_knowledge for 3-leg RRF.
    raw_query_vector / raw_sparse_vector add pre-rewrite RRF legs (see
    _search_knowledge) and should be passed only when the rewrite changed the
    query. ``query_text`` (the resolved query, main pass only) adds the
    translated and the alias-expanded leg when they apply; a failure while
    building them leaves them out and the search runs as it would without.
    """
    extra_legs: list[tuple[list[float], SparseVector | None]] = []
    if query_text:
        try:
            for text in await _extra_query_texts(query_text, request):
                dense, sparse = await asyncio.gather(embed_single(text), embed_sparse(text))
                extra_legs.append((dense, sparse))
        except Exception as exc:
            logger.warning("retrieval_extra_legs_failed", error=type(exc).__name__)
            extra_legs = []
    # org, personal, both
    return await _search_knowledge(
        query_vector,
        request,
        candidates,
        sparse_vector,
        raw_query_vector=raw_query_vector,
        raw_sparse_vector=raw_sparse_vector,
        extra_legs=extra_legs,
    )
