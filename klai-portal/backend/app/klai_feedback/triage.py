from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import cross_org_session
from app.klai_feedback.models import FeedbackItem, FeedbackSubmission, FeedbackTriageSuggestion
from app.klai_feedback.service import FeedbackSubmissionNotFoundError, get_feedback_submission, search_feedback_items
from app.trace import get_trace_headers

logger = structlog.get_logger()

TRIAGE_PROMPT_VERSION = "feedback-triage-v1"
TriageClassifier = Callable[[FeedbackSubmission, list[FeedbackItem], str], Awaitable["TriageSuggestionDraft"]]

_TRIAGE_SYSTEM = """\
You triage first-party Klai product feedback for internal staff.
Return ONLY valid JSON with this exact structure:
{
  "classification": "feature|bug|ux_confusion|docs|support_pattern",
  "summary": "short internal summary in Dutch",
  "suggested_area": "short product area or null",
  "suggested_severity": "low|medium|high|urgent",
  "suggested_action": "link_existing|create_item|support|dismiss",
  "duplicate_candidates": [{"item_id": 123, "confidence": 0.0, "reason": "short reason"}]
}
Do not create roadmap/public copy. Do not invent item ids. Use only duplicate item ids from the provided candidates.
When a candidate is plausibly about the same problem or request, prefer suggested_action link_existing.
Only use create_item when no candidate is suitable.
For source assistant_problem, produce a bug proposal unless the message is clearly a support-only, configuration, or docs question.
For reproducible bugs without a matching candidate, prefer suggested_action create_item.
"""


@dataclass(frozen=True)
class TriageSuggestionDraft:
    classification: str | None
    summary: str | None
    suggested_area: str | None
    suggested_severity: str | None
    duplicate_candidates: list[dict]
    suggested_action: str | None


def _model_key(model: str) -> str:
    return f"{model}:{TRIAGE_PROMPT_VERSION}"


async def generate_feedback_triage_suggestion(
    db: AsyncSession,
    submission_id: int,
    *,
    classifier: TriageClassifier | None = None,
    model: str | None = None,
) -> FeedbackTriageSuggestion:
    model_name = model or settings.feedback_triage_model
    model_key = _model_key(model_name)
    existing = await get_existing_triage_suggestion(db, submission_id, model_key)
    if existing is not None:
        return existing

    submission = await get_feedback_submission(db, submission_id)
    candidates = await search_feedback_items(
        db,
        search=_candidate_search_text(submission.raw_text),
        status="triage",
        kind="all",
        limit=20,
    )
    draft = await (classifier or classify_feedback_submission)(submission, candidates, model_name)
    suggestion = FeedbackTriageSuggestion(
        submission_id=submission.id,
        classification=draft.classification,
        summary=draft.summary,
        suggested_area=draft.suggested_area,
        suggested_severity=draft.suggested_severity,
        duplicate_candidates_json={"candidates": draft.duplicate_candidates},
        suggested_action=draft.suggested_action,
        model=model_key,
    )
    db.add(suggestion)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await get_existing_triage_suggestion(db, submission_id, model_key)
        if existing is not None:
            return existing
        raise
    return suggestion


async def get_existing_triage_suggestion(
    db: AsyncSession,
    submission_id: int,
    model_key: str,
) -> FeedbackTriageSuggestion | None:
    return (
        await db.execute(
            select(FeedbackTriageSuggestion).where(
                FeedbackTriageSuggestion.submission_id == submission_id,
                FeedbackTriageSuggestion.model == model_key,
            )
        )
    ).scalar_one_or_none()


async def run_feedback_triage_for_submission(submission_id: int) -> None:
    async with cross_org_session() as db:
        try:
            await generate_feedback_triage_suggestion(db, submission_id)
        except FeedbackSubmissionNotFoundError:
            logger.warning("feedback_triage_submission_missing", submission_id=submission_id)
        except Exception:
            logger.warning("feedback_triage_failed", submission_id=submission_id, exc_info=True)


async def classify_feedback_submission(
    submission: FeedbackSubmission,
    candidates: list[FeedbackItem],
    model: str,
) -> TriageSuggestionDraft:
    raw = await _call_triage_llm(
        model=model,
        user=_build_triage_prompt(submission, candidates),
    )
    return _parse_triage_response(raw, {item.id for item in candidates})


async def _call_triage_llm(*, model: str, user: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.litellm_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.litellm_master_key}", **get_trace_headers()},
            json={
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": _TRIAGE_SYSTEM},
                    {"role": "user", "content": user},
                ],
            },
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])


def _build_triage_prompt(submission: FeedbackSubmission, candidates: list[FeedbackItem]) -> str:
    candidate_payload = [
        {
            "item_id": item.id,
            "kind": item.kind,
            "title": item.title,
            "summary": item.summary,
            "area": item.area,
            "status": item.status,
        }
        for item in candidates
    ]
    return json.dumps(
        {
            "submission": {
                "id": submission.id,
                "source": submission.source,
                "raw_text": submission.raw_text,
                "route_id": submission.route_id,
                "locale": submission.locale,
                "metadata": submission.metadata_json or {},
            },
            "candidate_items": candidate_payload,
        },
        ensure_ascii=False,
    )


def _parse_triage_response(raw: str, allowed_item_ids: set[int]) -> TriageSuggestionDraft:
    data = _parse_json_response(raw)
    duplicate_candidates = [
        candidate
        for candidate in _as_list(data.get("duplicate_candidates"))
        if isinstance(candidate, dict) and candidate.get("item_id") in allowed_item_ids
    ][:5]
    return TriageSuggestionDraft(
        classification=_coerce_choice(
            data.get("classification"),
            {"feature", "bug", "ux_confusion", "docs", "support_pattern"},
        ),
        summary=_coerce_text(data.get("summary"), 1000),
        suggested_area=_coerce_text(data.get("suggested_area"), 128),
        suggested_severity=_coerce_choice(data.get("suggested_severity"), {"low", "medium", "high", "urgent"}),
        duplicate_candidates=duplicate_candidates,
        suggested_action=_coerce_choice(
            data.get("suggested_action"),
            {"link_existing", "create_item", "support", "dismiss"},
        ),
    )


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else {}


def _coerce_choice(value: object, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _coerce_text(value: object, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:max_length] or None


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _candidate_search_text(raw_text: str) -> str | None:
    words = [word.strip(".,:;!?()[]{}\"'").lower() for word in raw_text.split()]
    meaningful = [word for word in words if len(word) >= 4]
    return " ".join(meaningful[:4]) or None
