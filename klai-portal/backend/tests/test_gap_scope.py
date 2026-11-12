"""A turn against the employee's own personal knowledge base is not a gap of the organisation's."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.services import retrieval_context
from app.services.chat_profile import ChatProfile


def _turn(scope: str) -> MagicMock:
    t = MagicMock()
    t.profile = ChatProfile(surface="internal", kb_scope=cast(Any, scope), user_id="user-1")
    t.internal = True
    return t


@pytest.mark.parametrize(("scope", "recorded"), [("personal", 0), ("both", 1), ("org", 1)])
def test_gap_is_recorded_unless_the_turn_searched_only_the_personal_knowledge_base(
    monkeypatch, scope: str, recorded: int
) -> None:
    scheduled: list[dict] = []
    monkeypatch.setattr(retrieval_context, "_schedule_gap_event", lambda **kw: scheduled.append(kw))
    found = MagicMock(retrieval_user_id=None, chunks=[], retrieval_ms=1)

    retrieval_context._register_gap(cast(Any, _turn(scope)), cast(Any, found))

    assert len(scheduled) == recorded
