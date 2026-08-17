"""Two thumbs on the same chunk must count as two.

``apply_quality_score`` reads a chunk's payload, computes the new running
average, and writes it back. Qdrant has no atomic increment for payload fields,
so those three steps are a classic read-modify-write: two feedback events
interleaving on the same chunk both read ``feedback_count = N`` and both write
``N + 1``. One vote disappears with no error anywhere.

That matters more than the raw probability suggests. The count is not just a
statistic -- it is the cold-start gate (SPEC-KB-015 r.118). A chunk stuck one
vote short of three stays unboosted, and the vote that would have crossed the
threshold is the one that got lost.

The fake below awaits between read and write, which is what makes the
interleaving deterministic instead of a coin flip. Without the lock in
apply_quality_score, ``test_two_concurrent_ratings_both_count`` fails with
count 1.
"""

import asyncio
from unittest.mock import patch

import pytest

CHUNK = "chunk-shared"


class _FakeQdrant:
    """Minimal stand-in for the two Qdrant REST calls, with a shared store.

    Deliberately yields the event loop inside the read. A real HTTP round-trip
    yields too, so this reproduces the production interleaving rather than
    inventing one.
    """

    def __init__(self, *, quality_score: float = 0.5, feedback_count: int = 0) -> None:
        self.store = {"quality_score": quality_score, "feedback_count": feedback_count}
        self.writes: list[dict] = []

    async def post(self, url: str, json: dict):  # `json` mirrors httpx.posts kwarg name
        if url.endswith("/points"):
            # Snapshot BEFORE yielding, then yield. That is the real shape: a
            # read returns the value as of the moment the server answered, and
            # the caller does its arithmetic after a network delay during which
            # another writer can land. Snapshotting after the yield would let
            # the second reader see the first writer's result and the race
            # would quietly fail to reproduce.
            snapshot = dict(self.store)
            await asyncio.sleep(0)
            return _FakeResponse({"result": [{"id": CHUNK, "payload": snapshot}]})
        if url.endswith("/points/payload"):
            payload = json["payload"]
            self.store.update(payload)
            self.writes.append(dict(payload))
            return _FakeResponse({"result": {}})
        raise AssertionError(f"unexpected Qdrant call: {url}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeResponse:
    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        return None


@pytest.mark.asyncio
async def test_two_concurrent_ratings_both_count():
    """The race, pinned. Two thumbsUp on one chunk -> feedback_count 2."""
    from app.services import quality_scorer

    fake = _FakeQdrant()

    with patch.object(quality_scorer.httpx, "AsyncClient", lambda **_kw: fake):
        await asyncio.gather(
            quality_scorer.apply_quality_score([CHUNK], "thumbsUp", 42),
            quality_scorer.apply_quality_score([CHUNK], "thumbsUp", 42),
        )

    assert fake.store["feedback_count"] == 2, (
        "a concurrent vote was lost: both calls read the same count and wrote "
        f"the same increment (writes: {fake.writes})"
    )
    assert fake.store["quality_score"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_mixed_ratings_average_over_both_votes():
    """One up, one down, from count 0 -> 0.5 over two votes, not one.

    Order does not matter here: (0*0+1)/1 then (1*1+0)/2 = 0.5, and the reverse
    gives (0*0+0)/1 then (0*1+1)/2 = 0.5.
    """
    from app.services import quality_scorer

    fake = _FakeQdrant()

    with patch.object(quality_scorer.httpx, "AsyncClient", lambda **_kw: fake):
        await asyncio.gather(
            quality_scorer.apply_quality_score([CHUNK], "thumbsUp", 42),
            quality_scorer.apply_quality_score([CHUNK], "thumbsDown", 42),
        )

    assert fake.store["feedback_count"] == 2
    assert fake.store["quality_score"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_serialisation_does_not_swallow_the_second_vote_on_a_cold_chunk():
    """Three votes must reach the cold-start threshold, not stall at two.

    This is the consequence that makes the race worth fixing: feedback_count is
    the gate quality_boost opens at 3 (SPEC-KB-015 r.118). A lost vote is not a
    rounding error, it is a chunk that never starts being ranked on its
    feedback.
    """
    from app.services import quality_scorer

    fake = _FakeQdrant()

    with patch.object(quality_scorer.httpx, "AsyncClient", lambda **_kw: fake):
        await asyncio.gather(*(quality_scorer.apply_quality_score([CHUNK], "thumbsUp", 42) for _ in range(3)))

    assert fake.store["feedback_count"] == 3


@pytest.mark.asyncio
async def test_a_qdrant_failure_still_releases_the_lock():
    """A raising update must not wedge every later update behind a held lock.

    apply_quality_score swallows errors by contract (REQ-KB-015-18); the danger
    is the lock, not the exception. If the first call leaves it held, the
    feedback loop stops silently for the lifetime of the process.
    """
    from app.services import quality_scorer

    class _Exploding(_FakeQdrant):
        async def post(self, url: str, json: dict):
            raise RuntimeError("qdrant unreachable")

    with patch.object(quality_scorer.httpx, "AsyncClient", lambda **_kw: _Exploding()):
        await quality_scorer.apply_quality_score([CHUNK], "thumbsUp", 42)

    fake = _FakeQdrant()
    with patch.object(quality_scorer.httpx, "AsyncClient", lambda **_kw: fake):
        await asyncio.wait_for(quality_scorer.apply_quality_score([CHUNK], "thumbsUp", 42), timeout=2)

    assert fake.store["feedback_count"] == 1
