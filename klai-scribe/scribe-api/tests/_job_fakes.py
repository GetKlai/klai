"""In-memory stand-ins for the database used by the background-job tests.

There is no Postgres in the unit-test run, so rows live in a dict shared by
the route's session and the worker's session. `fake_claim` mirrors the
predicate of `jobs._CLAIM_SQL`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.models.transcription import Transcription


class FakeSession:
    def __init__(self, store: dict[str, Transcription]) -> None:
        self.store = store
        self.select_result: Transcription | None = None
        self.commits = 0

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def add(self, record: Transcription) -> None:
        # The column default the INSERT would apply.
        if record.attempts is None:
            record.attempts = 0
        self.store[record.id] = record

    async def get(self, _model, txn_id: str, **_kwargs) -> Transcription | None:
        return self.store.get(txn_id)

    async def execute(self, _statement, _params=None):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self.select_result
        return result

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    async def refresh(self, _record: Transcription) -> None:
        return None


def fake_claim(store: dict[str, Transcription]):
    async def claim(_session) -> str | None:
        stale = datetime.now(UTC) - timedelta(minutes=15)
        candidates = sorted(
            (
                r
                for r in store.values()
                if r.status == "processing"
                and r.audio_path is not None
                and r.attempts < 3
                and (r.claimed_at is None or r.claimed_at < stale)
            ),
            key=lambda r: r.created_at,
        )
        if not candidates:
            return None
        record = candidates[0]
        record.claimed_at = datetime.now(UTC)
        record.attempts += 1
        return record.id

    return claim


def make_record(txn_id: str = "txn_a", **overrides) -> Transcription:
    fields = {
        "id": txn_id,
        "user_id": "user1",
        "org_id": "org1",
        "name": None,
        "status": "processing",
        "audio_path": f"user1/{txn_id}.wav",
        "attempts": 0,
        "claimed_at": None,
        "language_hint": None,
        "error_reason": None,
        "created_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Transcription(**fields)


class Result:
    text = "hallo dit is een korte opname over het nieuwe plan"
    language = "nl"
    duration_seconds = 3.0
    inference_time_seconds = 0.4
    provider = "test-provider"
    model = "test-model"
