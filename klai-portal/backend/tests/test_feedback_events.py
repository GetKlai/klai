"""RED: Verify PortalFeedbackEvent model construction.

SPEC-KB-015 REQ-KB-015-10/12/13: feedback events table with RLS, idempotency, no user_id.
"""


def test_feedback_event_model_construction():
    """PortalFeedbackEvent can be instantiated with required fields."""
    from app.models.feedback_events import PortalFeedbackEvent

    event = PortalFeedbackEvent(
        org_id=1,
        conversation_id="conv-123",
        message_id="msg-456",
        rating="thumbsUp",
        chunk_ids=["chunk-a", "chunk-b"],
        correlated=True,
    )
    assert event.org_id == 1
    assert event.rating == "thumbsUp"
    assert event.correlated is True
    assert event.chunk_ids == ["chunk-a", "chunk-b"]
    assert event.conversation_id == "conv-123"
    assert event.message_id == "msg-456"


def test_feedback_event_model_optional_fields():
    """Optional fields default correctly."""
    from app.models.feedback_events import PortalFeedbackEvent

    event = PortalFeedbackEvent(
        org_id=1,
        conversation_id="conv-123",
        message_id="msg-456",
        rating="thumbsDown",
        correlated=False,
    )
    assert event.tag is None
    assert event.feedback_text is None
    assert event.model_alias is None
    assert event.chunk_ids is None or event.chunk_ids == []


def test_feedback_event_has_no_user_id_column():
    """Privacy: PortalFeedbackEvent must NOT have a user_id column."""
    from app.models.feedback_events import PortalFeedbackEvent

    columns = {c.name for c in PortalFeedbackEvent.__table__.columns}
    assert "user_id" not in columns, "user_id must NOT be stored in feedback_events (privacy)"


def test_feedback_event_has_unique_constraint():
    """Idempotency: (message_id, conversation_id) must be unique."""
    from app.models.feedback_events import PortalFeedbackEvent

    table = PortalFeedbackEvent.__table__
    unique_constraints = [
        c
        for c in table.constraints
        if hasattr(c, "columns") and {col.name for col in c.columns} == {"message_id", "conversation_id"}
    ]
    assert len(unique_constraints) > 0, "Missing unique constraint on (message_id, conversation_id)"
