"""Knowledge-gap event write-path shared by all telemetry producers.

Extracted verbatim from POST /internal/v1/gap-events so the HTTP endpoint
(LiteLLM hook → LibreChat, klai-knowledge-mcp → third-party clients) and
in-process callers — the widget / partner chatpad in
``app.services.partner_chat.retrieve_context`` — run through the identical
privacy-gating and tenant-scoping code. Portal-internal callers MUST call
this function directly, never loop back over HTTP to portal-api's own
endpoint.

SPEC-PRIVACY-QUERY-SHADOW-001 REQ-8: the canonical per-tenant
``telemetry_level`` is always re-fetched from ``portal_orgs`` here. Callers
pass query text and retrieval metadata, never a telemetry level — an
upstream-supplied level is never trusted.

- off    → no row inserted
- shadow → row inserted with query_text='[REDACTED:shadow]'
- full   → row inserted with the literal query_text (existing behavior)

# @MX:WARN: Fire-and-forget callers must pass an RLS-scoped session — use
# @MX:WARN: app.core.database.tenant_scoped_session when no request session
# @MX:WARN: is at hand (see partner_chat._schedule_gap_event for the pattern).
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx
import structlog
from sqlalchemy import ColumnElement, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import set_tenant
from app.models.portal import PortalOrg
from app.models.retrieval_gaps import PortalRetrievalGap
from app.trace import get_trace_headers

logger = structlog.get_logger()

# Producers whose row is a verdict that the visitor went unhelped: a person in
# the answer review (app_activity), or the conversation judge.
REVIEW_CALLER_CLIENT_ID = "human-review"
JUDGE_CALLER_CLIENT_ID = "quality-judge"

# How many open groups the grouping judge sees per question. They are ranked by
# embedding similarity first, so these are the closest; 30 keeps the prompt
# short. Not tuned yet: the judge backfill is the first run to measure it on.
PROMPT_CANDIDATES = 30
# Open groups read per fold before ranking. Far above one tenant's open-group
# count today (under 100), so ranking, not truncation, decides what the judge sees.
_CANDIDATE_POOL = 1000
_EMBEDDING_MODEL = "klai-bge-m3"
_EMBEDDING_TIMEOUT_S = 30.0
# Inputs per embeddings request, under the embedding server's batch cap (256).
_EMBEDDING_BATCH = 128


def shows_unmet_need() -> ColumnElement[bool]:
    """Rows that show a visitor went unhelped, which is what the inbox lists.

    A support-case finding, or a readable row that is either a verdict (human
    review, conversation judge) or a search that found nothing at all. A low
    retrieval score ("soft") on its own is left out: most such answers were
    fine, and the judge files its own row for the ones that were not. One
    definition, so the inbox, the grouping candidates and the judge's
    duplicate check all mean the same rows.
    """
    return or_(
        PortalRetrievalGap.support_case_id.isnot(None),
        and_(
            PortalRetrievalGap.query_text.not_like("[REDACTED:%"),
            or_(
                PortalRetrievalGap.gap_type == "hard",
                PortalRetrievalGap.caller_client_id.in_((REVIEW_CALLER_CLIENT_ID, JUDGE_CALLER_CLIENT_ID)),
            ),
        ),
    )


GapEventOutcome = Literal["created", "skipped", "not_found"]


@dataclass(frozen=True, slots=True)
class GapEventResult:
    """Outcome of one ``record_gap_event`` call.

    ``org_id`` is the resolved internal (integer) org id — ``None`` when the
    Zitadel org could not be mapped. The HTTP endpoint needs it for the
    internal-call audit; in-process callers can ignore it.
    """

    outcome: GapEventOutcome
    org_id: int | None = None
    # Row id of the gap just written, so a caller that must point at it (a
    # human review, SPEC-KNOWLEDGE-ACTIVITY-001 §4.5) does not have to read
    # the row back through the telemetry redaction.
    gap_id: int | None = None


async def record_gap_event(
    db: AsyncSession,
    *,
    zitadel_org_id: str,
    user_id: str,
    query_text: str,
    gap_type: str,
    top_score: float | None = None,
    nearest_kb_slug: str | None = None,
    chunks_retrieved: int = 0,
    retrieval_ms: int = 0,
    taxonomy_node_ids: list[int] | None = None,
    caller_client_id: str | None = None,
    conversation_id: int | None = None,
    language: str | None = None,
    audience: str | None = None,
    evidence: dict | None = None,
    occurred_at: datetime | None = None,
) -> GapEventResult:
    """Insert one knowledge-gap row, gated by the org's telemetry level.

    Resolves the org by Zitadel id, binds the tenant scope for RLS
    (``set_tenant``), applies the SPEC-PRIVACY-QUERY-SHADOW-001 REQ-8
    off/shadow/full gating, and commits through the caller-provided session.
    Never raises on a missing org — returns ``outcome='not_found'`` so the
    HTTP layer can decide how to surface it (404) while in-process callers
    just log and move on.

    ``conversation_id`` / ``language`` are provenance (SPEC-KNOWLEDGE-ACTIVITY-001
    §4.5): the widget conversation the question came from and the language it
    was asked in. ``audience`` ('customer' | 'internal') separates groups, since
    a customer and an employee missing knowledge are two editorial jobs;
    ``evidence`` carries the producer's own reference (the judge's verdict).
    All stay NULL for callers that do not know them. ``occurred_at`` defaults to
    now; a producer filing late (the judge backfill) passes when it was asked.
    """
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == zitadel_org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        return GapEventResult("not_found")
    await set_tenant(db, org.id)

    # REQ-8: 'off' → skip the INSERT entirely. Tenant accepts the
    # support-side trade-off; the endpoint still responds 200 to keep the
    # idempotent contract for fire-and-forget callers.
    if org.telemetry_level == "off":
        return GapEventResult("skipped", org.id)

    # REQ-8: 'shadow' → REDACT the literal query text. The matching
    # telemetry.query_shadow row (written by retrieval-api) carries the
    # embedding + features for support-team triage.
    effective_query_text = query_text if org.telemetry_level == "full" else "[REDACTED:shadow]"

    # SPEC-RAG-GAP-GROUPING: every producer writes a question_key so the inbox
    # can group on it instead of exact query text (app/api/app_gaps.py). This is
    # the cheap literal-text key from support_cases._question_key — no model
    # call on the write path. A paraphrase of an existing open group is folded
    # onto that group's key asynchronously below, off the request path.
    from app.services.support_cases import _question_key

    question_key = _question_key(
        question=effective_query_text, language=language, kb_slug=nearest_kb_slug, audience=audience
    )

    gap = PortalRetrievalGap(
        org_id=org.id,
        user_id=user_id,
        query_text=effective_query_text,
        gap_type=gap_type,
        top_score=top_score,
        nearest_kb_slug=nearest_kb_slug,
        chunks_retrieved=chunks_retrieved,
        retrieval_ms=retrieval_ms,
        taxonomy_node_ids=taxonomy_node_ids,
        caller_client_id=caller_client_id,
        conversation_id=conversation_id,
        language=language,
        question_key=question_key,
        audience=audience,
        evidence=evidence,
        **({"occurred_at": occurred_at} if occurred_at is not None else {}),
    )
    db.add(gap)
    await db.commit()

    # Fold a paraphrase (or a different producer's finding) into an existing
    # open group, off the request path. Redacted 'shadow' text carries no
    # signal an LLM could compare — and would risk merging unrelated groups
    # under one constant placeholder string — so only 'full' telemetry runs
    # this; every other row still groups on its own literal key above. A row
    # without a knowledge base (the judge's) folds among groups without one.
    if org.telemetry_level == "full":

        async def _group_gap(
            gap_id: int, org_int_id: int, kb_slug: str | None, question: str, lang: str | None, base_key: str
        ) -> None:
            """Background wrapper, same pattern as ``_classify_gap`` below. A
            failure (timeout, malformed model output, RLS mismatch) is logged
            and leaves the row on its own literal key: never lost, just not
            merged, and never raised across the request boundary."""
            try:
                matched = await fold_into_open_group(
                    org_id=org_int_id,
                    kb_slug=kb_slug,
                    question=question,
                    language=lang,
                    audience=audience,
                    base_key=base_key,
                )
                if matched is not None:
                    # Ids only: the key carries the normalized question, which has no business in an app log.
                    logger.info("gap_grouping_merged", gap_id=gap_id, org_id=org_int_id)
            except Exception:
                logger.exception("gap_grouping_failed", gap_id=gap_id)

        _grouping_task = asyncio.create_task(  # noqa: RUF006
            _group_gap(gap.id, org.id, nearest_kb_slug, effective_query_text, language, question_key)
        )

    # SPEC-KB-022 R6 + SPEC-KB-026 R4: async gap classification via knowledge-ingest
    if taxonomy_node_ids is None and nearest_kb_slug:

        async def _classify_gap(
            gap_id: int,
            org_int_id: int,
            org_zitadel_id: str,
            query_text: str,
            kb_slug: str,
        ) -> None:
            """Classify gap query against KB taxonomy via knowledge-ingest.

            Background task on a fresh session: `tenant_scoped_session`
            guarantees the connection is pinned and app.current_org_id is
            set before the UPDATE, so RLS does not silently filter the row
            to zero. rowcount==0 raises; the RLS guard event listener also
            catches this as a safety net.
            """
            try:
                from app.core.database import tenant_scoped_session
                from app.services.knowledge_ingest_client import classify_gap_taxonomy

                node_ids = await classify_gap_taxonomy(org_zitadel_id, kb_slug, query_text)
                if not node_ids:
                    return

                async with tenant_scoped_session(org_int_id) as session:
                    result = await session.execute(
                        update(PortalRetrievalGap)
                        .where(PortalRetrievalGap.id == gap_id)
                        .values(taxonomy_node_ids=node_ids)
                    )
                    if result.rowcount == 0:  # type: ignore[attr-defined]
                        raise RuntimeError(
                            f"gap_classification UPDATE matched 0 rows "
                            f"(gap_id={gap_id}, org_id={org_int_id}) — "
                            f"likely RLS/tenant-context mismatch"
                        )
                    await session.commit()

                logger.info(
                    "gap_classification_complete: gap_id=%s, node_ids=%s",
                    gap_id,
                    node_ids,
                )
            except Exception:
                logger.exception(
                    "gap_classification_failed: gap_id=%s",
                    gap_id,
                )

        _task = asyncio.create_task(  # noqa: RUF006
            _classify_gap(
                gap.id,
                org.id,
                zitadel_org_id,
                query_text,
                nearest_kb_slug,
            )
        )

    return GapEventResult("created", org.id, gap.id)


def compatible_scope(finding: dict, candidate: dict) -> bool:
    """Whether the grouping judge may put ``finding`` in ``candidate``'s group.

    Language is a hard separator; audience only separates when both sides know
    it and disagree: an unrecorded audience (most chat/telemetry findings, and
    any support finding the analyzer didn't classify) must not be its own
    bucket walled off from every other one. Lives here rather than in
    support_gap_grouping so the fold can shortlist with the same rule without
    importing the analyzer.
    """
    if finding["language"] != candidate.get("language"):
        return False
    finding_audience, candidate_audience = finding.get("audience"), candidate.get("audience")
    return finding_audience is None or candidate_audience is None or finding_audience == candidate_audience


async def _embed(texts: list[str]) -> list[list[float]]:
    """BGE-M3 vectors through LiteLLM, the same endpoint and key as the judges."""
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_EMBEDDING_TIMEOUT_S) as client:
        for start in range(0, len(texts), _EMBEDDING_BATCH):
            resp = await client.post(
                f"{settings.litellm_base_url}/v1/embeddings",
                headers={"Authorization": f"Bearer {settings.litellm_master_key}", **get_trace_headers()},
                json={"model": _EMBEDDING_MODEL, "input": texts[start : start + _EMBEDDING_BATCH]},
            )
            resp.raise_for_status()
            vectors += [item["embedding"] for item in sorted(resp.json()["data"], key=lambda item: item["index"])]
    return vectors


def _cosine(a: list[float], b: list[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else 0.0


async def _closest_candidates(question: str, candidates: list[dict]) -> list[dict]:
    """The PROMPT_CANDIDATES groups most similar to ``question``, closest first."""
    if len(candidates) <= PROMPT_CANDIDATES:
        return candidates
    vectors = await _embed([question, *(c["question"] for c in candidates)])
    ranked = sorted(zip(candidates, vectors[1:], strict=True), key=lambda cv: -_cosine(vectors[0], cv[1]))
    return [candidate for candidate, _vector in ranked[:PROMPT_CANDIDATES]]


async def fold_into_open_group(
    *,
    org_id: int,
    kb_slug: str | None,
    question: str,
    language: str | None,
    audience: str | None,
    base_key: str,
) -> str | None:
    """Ask the grouping judge whether the group on ``base_key`` is the same
    need as an existing open group, and fold it onto that group's key when the
    judge verifies it. Returns the key it was folded onto, or None.

    Shared by the write path (one new row, in the background) and the one-off
    regroup script over existing rows (scripts/regroup_open_gaps.py), so both
    merge by the same rule. Reuses ``support_gap_grouping.group_findings``
    rather than a second mechanism.

    The candidate snapshot is read before the model call, so a group closed in
    between can still receive this group and reappear as open. That is
    accepted: without the merge the same rows would sit in the list as their
    own open group anyway, so the user has one thing to close either way, and
    no row is closed or lost by the race.
    """
    from app.core.database import tenant_scoped_session
    from app.services.support_cases import _open_group_candidates
    from app.services.support_gap_grouping import group_findings

    async with tenant_scoped_session(org_id) as session:
        candidates = await _open_group_candidates(
            session,
            org_id=org_id,
            kb_slug=kb_slug,
            exclude_case_id=None,
            exclude_question_key=base_key,
            limit=_CANDIDATE_POOL,
        )
    # Diagnosis is a placeholder: chat/widget/MCP telemetry has no content
    # diagnosis, and the grouping judge no longer matches on it; it only needs
    # a value outside _NON_GAP_DIAGNOSES so the row is treated as an actual gap.
    finding = {"question": question, "diagnosis": "missing", "language": language, "audience": audience}
    # Rank only groups the judge could accept (same language, audience not in
    # conflict): otherwise close but incompatible groups fill the shortlist and
    # push out the one that could match.
    candidates = [c for c in candidates if compatible_scope(finding, c)]
    if not candidates:
        return None
    candidates = await _closest_candidates(question, candidates)
    matched = (await group_findings([finding], candidates))[0].get("group_question_key")
    if matched is None or matched == base_key:
        return None

    # Every open row on the key moves, not only the asking row: rows on the same
    # key are the same question, and leaving them behind would split it over two
    # groups. Closed rows keep their key, so a fold never rewrites closed history.
    async with tenant_scoped_session(org_id) as session:
        result = await session.execute(
            update(PortalRetrievalGap)
            .where(
                PortalRetrievalGap.org_id == org_id,
                PortalRetrievalGap.question_key == base_key,
                PortalRetrievalGap.resolved_at.is_(None),
            )
            .values(question_key=matched)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise RuntimeError(f"gap_grouping UPDATE matched 0 rows (org_id={org_id})")
        await session.commit()
    return matched
