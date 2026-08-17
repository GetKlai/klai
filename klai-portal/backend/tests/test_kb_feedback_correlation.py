"""The correlation step itself, unmocked, against a real Redis.

Why this file exists next to test_kb_feedback_endpoint.py
---------------------------------------------------------

That file patches ``find_correlated_log`` and asserts what the endpoint does
with its answer. Necessary, but it means the correlation step -- Redis key
construction, which identity the key is built from, and the timestamp window --
was never executed by any test. Both causes of the four-month outage lived
exactly there:

  1. The endpoint looked the log up under LibreChat's Mongo ObjectId while
     retrieval had written it under the Zitadel subject. Two namespaces, so the
     key never matched. 55 of 58 events came back uncorrelated.
  2. The forward sent the moment of the click instead of the message's own
     createdAt, which falls outside the SPEC's [-60s, +10s] window as soon as
     someone rates a message that is not brand new.

Neither is visible to a test that patches the function they live in. Both fail
here.

These tests write through the real ``write_retrieval_log`` and read through the
real ``find_correlated_log``, on the shared fakeredis fixture. Only the layers
that are genuinely not under test are patched: auth, the DB, and the two
fire-and-forget side effects.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The retrieval log is keyed by the Zitadel subject, because that is the
# identity the retrieval path knows. LibreChat's own user id is a Mongo
# ObjectId from an unrelated namespace -- the two must never be confused,
# which is the whole point of these tests.
ZITADEL_SUBJECT = "319481925918031876"
LIBRECHAT_OBJECT_ID = "68b1f0c2e4b0a1d2c3f4a5b6"
ORG_ID = 42
CHUNK_IDS = ["chunk-a", "chunk-b", "chunk-c"]


@pytest.fixture
def mock_org():
    org = MagicMock()
    org.id = ORG_ID
    org.zitadel_org_id = "zit-org-1"
    org.librechat_container = "tenant-abc"
    return org


def _mock_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


async def _seed_retrieval_log(*, user_id: str, retrieved_at: datetime) -> None:
    """Write a log entry through the production writer, not a hand-built key.

    Building the Redis key by hand in the test would let the writer and the
    reader drift apart while both tests stayed green -- the same two-sided
    mistake this file exists to catch.
    """
    from app.services.retrieval_log import write_retrieval_log

    await write_retrieval_log(
        org_id=ORG_ID,
        user_id=user_id,
        chunk_ids=CHUNK_IDS,
        reranker_scores=[0.91, 0.84, 0.72],
        query_resolved="hoe vraag ik verlof aan",
        embedding_model_version="bge-m3-v1",
        retrieved_at=retrieved_at,
    )


async def _post_feedback(body, mock_org):
    """Call the endpoint with correlation live. Returns (schedule_mock, emit_mock)."""
    from app.api.internal import post_kb_feedback

    mock_request = MagicMock()
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_mock_result(mock_org))
    mock_db.commit = AsyncMock()

    # The idempotency check and the retrieval log share the same pool in
    # production, so the fake is handed to both -- a GET on an unset
    # idempotency key returns None, which is exactly "not seen before".
    from app.services import redis_client

    with (
        patch("app.api.internal._require_internal_token"),
        patch("app.api.internal.get_redis_pool", side_effect=redis_client.get_redis_pool),
        patch("app.api.internal.set_tenant"),
        patch("app.api.internal.emit_event") as mock_emit,
        patch("app.api.internal.schedule_quality_update") as mock_schedule,
    ):
        await post_kb_feedback(body=body, request=mock_request, db=mock_db)
    return mock_schedule, mock_emit


def _body(**overrides):
    from app.api.internal import KbFeedbackIn

    defaults = {
        "conversation_id": "conv-1",
        "message_id": "msg-1",
        "message_created_at": datetime.now(UTC),
        "rating": "thumbsUp",
        "librechat_user_id": LIBRECHAT_OBJECT_ID,
        "librechat_tenant_id": "tenant-abc",
        "identity_user_id": ZITADEL_SUBJECT,
    }
    defaults.update(overrides)
    return KbFeedbackIn(**defaults)


@pytest.mark.asyncio
async def test_identity_id_correlates_against_the_log_retrieval_wrote(fake_redis, mock_org):
    """The whole chain: retrieval writes, feedback finds it, Qdrant gets scheduled."""
    now = datetime.now(UTC)
    await _seed_retrieval_log(user_id=ZITADEL_SUBJECT, retrieved_at=now - timedelta(seconds=5))

    schedule, emit = await _post_feedback(_body(message_created_at=now), mock_org)

    schedule.assert_called_once_with(CHUNK_IDS, "thumbsUp", ORG_ID)
    assert emit.call_args[1]["properties"]["correlated"] is True
    assert emit.call_args[1]["properties"]["chunk_count"] == len(CHUNK_IDS)


@pytest.mark.asyncio
async def test_librechat_object_id_alone_finds_nothing(fake_redis, mock_org):
    """The original bug, pinned.

    Retrieval writes under the Zitadel subject. A forward that only knows
    LibreChat's ObjectId looks under a key that was never written, gets no
    error, and stores the feedback as uncorrelated. That is what ran for four
    months. If someone drops identity_user_id from the forward again, this test
    keeps passing (correctly -- it asserts the fallback is inert) while the test
    above starts failing.
    """
    now = datetime.now(UTC)
    await _seed_retrieval_log(user_id=ZITADEL_SUBJECT, retrieved_at=now - timedelta(seconds=5))

    schedule, emit = await _post_feedback(_body(message_created_at=now, identity_user_id=None), mock_org)

    schedule.assert_not_called()
    assert emit.call_args[1]["properties"]["correlated"] is False
    assert emit.call_args[1]["properties"]["chunk_count"] == 0


@pytest.mark.asyncio
async def test_click_time_on_an_older_message_falls_outside_the_window(fake_redis, mock_org):
    """The second cause, pinned.

    SPEC-KB-015 r.71 correlates on the MESSAGE's createdAt, within
    [-60s, +10s] of the retrieval. Sending the click time instead works for a
    message rated immediately and silently stops working the moment a user
    scrolls up and rates an older answer -- the retrieval is then further back
    than the window allows.
    """
    now = datetime.now(UTC)
    message_created_at = now - timedelta(minutes=10)
    await _seed_retrieval_log(user_id=ZITADEL_SUBJECT, retrieved_at=message_created_at - timedelta(seconds=3))

    # Correct: the message's own timestamp still correlates ten minutes later.
    schedule, emit = await _post_feedback(_body(message_created_at=message_created_at), mock_org)
    schedule.assert_called_once_with(CHUNK_IDS, "thumbsUp", ORG_ID)
    assert emit.call_args[1]["properties"]["correlated"] is True

    # Wrong: the click time is 10 minutes past the retrieval, far outside the
    # 60-second window.
    schedule, emit = await _post_feedback(_body(message_id="msg-2", message_created_at=now), mock_org)
    schedule.assert_not_called()
    assert emit.call_args[1]["properties"]["correlated"] is False


@pytest.mark.asyncio
async def test_window_boundaries(fake_redis, mock_org):
    """r.71 defines [message_created_at - 60s, +10s]. Pin both edges."""
    now = datetime.now(UTC)

    # 59s before the message: inside.
    await _seed_retrieval_log(user_id=ZITADEL_SUBJECT, retrieved_at=now - timedelta(seconds=59))
    schedule, _ = await _post_feedback(_body(message_created_at=now), mock_org)
    schedule.assert_called_once()

    # 61s before: outside. A fresh key keeps the two cases independent.
    await fake_redis.delete(f"rl:{ORG_ID}:{ZITADEL_SUBJECT}")
    await _seed_retrieval_log(user_id=ZITADEL_SUBJECT, retrieved_at=now - timedelta(seconds=61))
    schedule, _ = await _post_feedback(_body(message_id="msg-3", message_created_at=now), mock_org)
    schedule.assert_not_called()


@pytest.mark.asyncio
async def test_a_different_tenant_cannot_correlate_against_our_log(fake_redis, mock_org):
    """The key is org-scoped. Same subject, other org, must not match.

    Cheap to assert and worth pinning: the retrieval log holds chunk ids, and
    correlating across orgs would attach one tenant's feedback to another
    tenant's chunks.
    """
    now = datetime.now(UTC)
    await _seed_retrieval_log(user_id=ZITADEL_SUBJECT, retrieved_at=now - timedelta(seconds=5))

    other_org = MagicMock()
    other_org.id = 99
    other_org.zitadel_org_id = "zit-org-2"
    other_org.librechat_container = "tenant-other"

    schedule, emit = await _post_feedback(_body(message_created_at=now), other_org)

    schedule.assert_not_called()
    assert emit.call_args[1]["properties"]["correlated"] is False
