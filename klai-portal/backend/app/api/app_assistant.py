"""First-party Klai assistant intake API.

This router is intentionally app-authenticated and first-party only. It is
not part of the public partner/widget API surface.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.permissions import UserPermissions, get_caller
from app.services.events import emit_event

router = APIRouter(prefix="/api/app/assistant", tags=["app-assistant"])


class AssistantContextIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    raw_text: str = Field(..., min_length=3, max_length=4000)
    page_url: str = Field(..., min_length=1, max_length=2048)
    route_id: str | None = Field(default=None, max_length=512)
    locale: str = Field(default="nl", max_length=16)
    viewport: str | None = Field(default=None, max_length=32)


class AssistantQuestionIn(AssistantContextIn):
    pass


class AssistantFeedbackIn(AssistantContextIn):
    type: Literal["idea", "improvement", "confusing", "missing", "compliment", "other"] = "other"


class AssistantProblemReportIn(AssistantContextIn):
    severity: Literal["blocked", "workaround", "minor"] = "workaround"


class AssistantSubmitResponse(BaseModel):
    ok: bool = True


def _strip_url_query_and_fragment(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _request_context(request: Request) -> dict[str, str | None]:
    client_host = request.client.host if request.client else None
    referer = request.headers.get("referer")
    return {
        "user_agent": request.headers.get("user-agent"),
        "referer": _strip_url_query_and_fragment(referer) if referer else None,
        "client_host": client_host,
    }


def _base_properties(
    body: AssistantContextIn,
    *,
    perms: UserPermissions,
    request: Request,
) -> dict:
    return {
        "raw_text": body.raw_text,
        "page_url": _strip_url_query_and_fragment(body.page_url),
        "route_id": body.route_id,
        "locale": body.locale,
        "viewport": body.viewport,
        "org_slug": perms.org_slug,
        "role": perms.effective_role.value,
        "source": "klai_assistant",
        **_request_context(request),
    }


@router.post(
    "/questions",
    status_code=status.HTTP_201_CREATED,
    response_model=AssistantSubmitResponse,
)
async def submit_question(
    body: AssistantQuestionIn,
    request: Request,
    perms: UserPermissions = Depends(get_caller),
) -> AssistantSubmitResponse:
    """Capture a first-party Klai help question from the assistant launcher."""
    emit_event(
        "klai_assistant.question",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties=_base_properties(body, perms=perms, request=request),
    )
    return AssistantSubmitResponse()


@router.post(
    "/feedback",
    status_code=status.HTTP_201_CREATED,
    response_model=AssistantSubmitResponse,
)
async def submit_feedback(
    body: AssistantFeedbackIn,
    request: Request,
    perms: UserPermissions = Depends(get_caller),
) -> AssistantSubmitResponse:
    """Capture Klai product feedback from authenticated portal users."""
    emit_event(
        "klai_assistant.feedback",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={
            **_base_properties(body, perms=perms, request=request),
            "feedback_type": body.type,
        },
    )
    return AssistantSubmitResponse()


@router.post(
    "/problem-reports",
    status_code=status.HTTP_201_CREATED,
    response_model=AssistantSubmitResponse,
)
async def submit_problem_report(
    body: AssistantProblemReportIn,
    request: Request,
    perms: UserPermissions = Depends(get_caller),
) -> AssistantSubmitResponse:
    """Capture a Klai problem report with basic diagnostic context."""
    emit_event(
        "klai_assistant.problem_report",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={
            **_base_properties(body, perms=perms, request=request),
            "severity": body.severity,
        },
    )
    return AssistantSubmitResponse()
