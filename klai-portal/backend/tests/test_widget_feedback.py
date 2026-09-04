"""Tests for POST /partner/v1/widget/feedback + widget_turn_id plumbing.

Widget sessions rate a single assistant answer; the rating is stamped on
the assistant ``widget_messages`` row addressed by the ``turn_id`` that
the chat request carried (``widget_turn_id``, stored by record_widget_turn).

AC-tested:
- Request validation: turn_id shape + rating literal (None = withdrawn).
- Partner API keys are rejected (403) — the endpoint is widget-only and
  /partner/v1/feedback behaviour for keys stays untouched.
- The UPDATE is scoped to the caller's own conversation + org (session_key
  and org_id come from the verified JWT, never the body).
- A foreign/unmatched turn_id (another visitor's or another org's message)
  matches no row → 404, nothing committed.
- Withdraw (rating=None) writes NULL.
- Per-session sliding-window rate limit → 429 with Retry-After.
- record_widget_turn persists turn_id on the assistant INSERT (default None).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from helpers import make_partner_auth

WGT_ID = "wgt_" + "ab" * 20
TURN_ID = "0123456789abcdef" * 2
SESSION_KEY = "salted-session-key-hash"


def _make_widget_auth():
    auth = make_partner_auth()
    auth.key_id = WGT_ID
    auth.session_key = SESSION_KEY
    return auth


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_turn_id_shape_enforced():
    from pydantic import ValidationError

    from app.api.partner import WidgetFeedbackRequest

    with pytest.raises(ValidationError):
        WidgetFeedbackRequest(turn_id="not-hex", rating="thumbsUp")
    with pytest.raises(ValidationError):
        WidgetFeedbackRequest(turn_id="ab", rating="thumbsUp")  # too short
    with pytest.raises(ValidationError):
        WidgetFeedbackRequest(turn_id=TURN_ID, rating="great")
    # Valid: rated + withdrawn.
    assert WidgetFeedbackRequest(turn_id=TURN_ID, rating="thumbsDown").rating == "thumbsDown"
    assert WidgetFeedbackRequest(turn_id=TURN_ID, rating=None).rating is None


# ---------------------------------------------------------------------------
# Auth surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partner_api_key_rejected():
    """pk_live_ auth context (no wgt_ key_id) → 403; the widget feedback
    endpoint must not become a second write surface for partner keys."""
    from app.api.partner import WidgetFeedbackRequest, submit_widget_feedback

    auth = make_partner_auth(permissions={"chat": True, "feedback": True, "knowledge_append": False})

    with pytest.raises(HTTPException) as exc_info:
        await submit_widget_feedback(
            request=WidgetFeedbackRequest(turn_id=TURN_ID, rating="thumbsUp"),
            auth=auth,
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_widget_session_without_session_key_rejected():
    """Widget JWTs always carry a session_key; without one the caller cannot
    be pinned to a conversation, so the request is refused."""
    from app.api.partner import WidgetFeedbackRequest, submit_widget_feedback

    auth = _make_widget_auth()
    auth.session_key = None

    with pytest.raises(HTTPException) as exc_info:
        await submit_widget_feedback(
            request=WidgetFeedbackRequest(turn_id=TURN_ID, rating="thumbsUp"),
            auth=auth,
            db=AsyncMock(),
        )
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Scoped UPDATE + addressing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_update_is_scoped_to_own_conversation_and_org():
    from app.api.partner import WidgetFeedbackRequest, submit_widget_feedback

    auth = _make_widget_auth()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()

    with patch("app.api.partner.get_redis_pool", new=AsyncMock(return_value=None)):
        result = await submit_widget_feedback(
            request=WidgetFeedbackRequest(turn_id=TURN_ID, rating="thumbsUp"),
            auth=auth,
            db=db,
        )

    assert result == {"ok": True, "rating": "thumbsUp"}
    sql, params = db.execute.call_args.args
    assert "UPDATE widget_messages" in sql.text
    # Identity predicates come from the verified JWT, not the request body:
    # own org, own session_key, own widget.
    assert params["org_id"] == auth.org_id
    assert params["session_key"] == SESSION_KEY
    assert params["wgt_id"] == WGT_ID
    assert params["turn_id"] == TURN_ID
    assert params["rating"] == "thumbsUp"
    for clause in ("m.role = 'assistant'", "m.org_id", "c.org_id", "c.session_key", "m.turn_id"):
        assert clause in sql.text, f"UPDATE must carry the {clause!r} scoping predicate"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_feedback_on_foreign_turn_rejected():
    """A turn_id that belongs to another visitor's conversation or another
    org matches no row in the caller-scoped UPDATE → 404, no commit."""
    from app.api.partner import WidgetFeedbackRequest, submit_widget_feedback

    auth = _make_widget_auth()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    db.commit = AsyncMock()

    with patch("app.api.partner.get_redis_pool", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await submit_widget_feedback(
                request=WidgetFeedbackRequest(turn_id=TURN_ID, rating="thumbsUp"),
                auth=auth,
                db=db,
            )

    assert exc_info.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_feedback_withdraw_clears_rating():
    """rating=None (second click on the active button) writes NULL back."""
    from app.api.partner import WidgetFeedbackRequest, submit_widget_feedback

    auth = _make_widget_auth()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    db.commit = AsyncMock()

    with patch("app.api.partner.get_redis_pool", new=AsyncMock(return_value=None)):
        result = await submit_widget_feedback(
            request=WidgetFeedbackRequest(turn_id=TURN_ID, rating=None),
            auth=auth,
            db=db,
        )

    assert result == {"ok": True, "rating": None}
    _sql, params = db.execute.call_args.args
    assert params["rating"] is None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_rate_limited_returns_429():
    from app.api.partner import WidgetFeedbackRequest, submit_widget_feedback

    auth = _make_widget_auth()
    db = AsyncMock()
    redis = AsyncMock()

    with (
        patch("app.api.partner.get_redis_pool", new=AsyncMock(return_value=redis)),
        patch("app.api.partner.check_rate_limit", new=AsyncMock(return_value=(False, 7))) as mock_rl,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await submit_widget_feedback(
                request=WidgetFeedbackRequest(turn_id=TURN_ID, rating="thumbsUp"),
                auth=auth,
                db=db,
            )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "7"
    # Dedicated per-session bucket, not the shared per-widget chat window.
    bucket = mock_rl.await_args.args[1]
    assert bucket.startswith("widget_feedback:")
    assert SESSION_KEY in bucket
    db.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# turn_id plumbing: record_widget_turn persists it on the message INSERT
# ---------------------------------------------------------------------------


async def _capture_record_turn_params(**kwargs) -> list[dict]:
    from app.services.widget_audit import record_widget_turn

    captured: list[dict] = []

    async def _exec(sql, params=None):
        if params is not None:
            captured.append(dict(params))
        res = MagicMock()
        res.first.return_value = ("conv-uuid", 0)
        return res

    tenant_db = AsyncMock()
    tenant_db.execute = AsyncMock(side_effect=_exec)
    tenant_db.commit = AsyncMock()

    lookup_row = MagicMock()
    lookup_row.first.return_value = (42,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)

    with (
        patch("app.services.widget_audit.cross_org_session") as ctx_cross,
        patch("app.services.widget_audit.tenant_scoped_session") as ctx_tenant,
    ):
        ctx_cross.return_value.__aenter__ = AsyncMock(return_value=lookup_db)
        ctx_cross.return_value.__aexit__ = AsyncMock(return_value=False)
        ctx_tenant.return_value.__aenter__ = AsyncMock(return_value=tenant_db)
        ctx_tenant.return_value.__aexit__ = AsyncMock(return_value=False)

        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000001",
            session_key=SESSION_KEY,
            **kwargs,
        )
    return [p for p in captured if "role" in p]


@pytest.mark.asyncio
async def test_record_widget_turn_persists_turn_id():
    inserts = await _capture_record_turn_params(role="assistant", content="answer", turn_id=TURN_ID)
    assert inserts, "Expected the widget_messages INSERT params"
    assert inserts[0]["turn_id"] == TURN_ID


@pytest.mark.asyncio
async def test_record_widget_turn_turn_id_defaults_none():
    inserts = await _capture_record_turn_params(role="user", content="hi")
    assert inserts[0]["turn_id"] is None


@pytest.mark.asyncio
async def test_chat_request_widget_turn_id_is_optional():
    """Partner clients never send the field; validation must not break them."""
    from app.api.partner import ChatCompletionsRequest

    req = ChatCompletionsRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.widget_turn_id is None
    assert (
        ChatCompletionsRequest(
            messages=[{"role": "user", "content": "hi"}],
            widget_turn_id=TURN_ID,
        ).widget_turn_id
        == TURN_ID
    )
