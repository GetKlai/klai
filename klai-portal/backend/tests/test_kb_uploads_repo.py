"""Repository-level rules for kb_uploads that no route test can show.

``get_view_by_artifact`` is the gate for "may this artifact be replaced by
uploading a file?". It answers with the row whose ``created_by`` the route
then checks, so which row it picks IS the permission decision.

The queries are exercised against a stubbed session: what matters here is
the decision logic layered on top of them, not SQLAlchemy's SQL generation
(the model's own hybrid is covered by the compile check in the ORM).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.kb_uploads import KBUpload
from app.services import kb_uploads_repo


def _row(
    *,
    artifact_id: str | None,
    source_ref: str = "file:sha256:v1",
    target_path: str | None = None,
    created_by: str = "user-a",
    created_at: datetime | None = None,
) -> KBUpload:
    row = KBUpload(
        id=uuid.uuid4(),
        kb_id=42,
        org_id=1,
        created_by=created_by,
        filename="sip.md",
        extension=".md",
        mime="text/plain",
        bytes=10,
        source_ref=source_ref,
        target_path=target_path,
        status=kb_uploads_repo.STATUS_DONE,
        artifact_id=artifact_id,
    )
    row.created_at = created_at or datetime.now(UTC)
    row.updated_at = row.created_at
    return row


def _db(*results: object) -> AsyncMock:
    """Session stub returning one prepared result per execute() call."""
    db = AsyncMock()
    wrapped = []
    for r in results:
        holder = MagicMock()
        holder.scalar_one_or_none = MagicMock(return_value=r)
        wrapped.append(holder)
    db.execute = AsyncMock(side_effect=wrapped)
    return db


class TestGetViewByArtifact:
    @pytest.mark.asyncio
    async def test_returns_the_current_version(self) -> None:
        row = _row(artifact_id="art-v1")
        db = _db(row, None)

        view = await kb_uploads_repo.get_view_by_artifact(db, kb_id=42, org_id=1, artifact_id="art-v1")

        assert view is not None
        assert view.artifact_id == "art-v1"

    @pytest.mark.asyncio
    async def test_refuses_a_superseded_version(self) -> None:
        """A stale tab holds v1's artifact id after someone else shipped v2.

        Answering with v1's row would hand the route v1's ``created_by``, so
        whoever uploaded v1 could overwrite content they are not allowed to
        delete.
        """
        row = _row(artifact_id="art-v1", created_by="user-a")
        db = _db(row, uuid.uuid4())

        view = await kb_uploads_repo.get_view_by_artifact(db, kb_id=42, org_id=1, artifact_id="art-v1")

        assert view is None

    @pytest.mark.asyncio
    async def test_newness_only_counts_versions_that_produced_an_artifact(self) -> None:
        """A no-op replacement must not make the live version unaddressable.

        Replacing a file with identical content is deduped by
        knowledge-ingest, which answers without an artifact_id. That row can
        never be addressed itself, so if it counted as a newer version the
        source would go unreplaceable while the UI kept offering the action.

        Asserted on the emitted SQL rather than on a stubbed answer: the
        filter IS the behaviour here, and a stub that returns "nothing newer"
        would pass with or without it.
        """
        from sqlalchemy.dialects import postgresql

        row = _row(artifact_id="art-v1")
        db = _db(row, None)

        await kb_uploads_repo.get_view_by_artifact(db, kb_id=42, org_id=1, artifact_id="art-v1")

        newness_query = db.execute.await_args_list[1].args[0]
        sql = str(newness_query.compile(dialect=postgresql.dialect())).lower()
        assert "artifact_id is not null" in sql
        assert "artifact_id !=" in sql
        # Newness is judged per source, not per uploaded file.
        assert "coalesce(kb_uploads.target_path, kb_uploads.source_ref)" in sql

    @pytest.mark.asyncio
    async def test_returns_none_for_a_source_without_a_tracking_row(self) -> None:
        """URL sources and pasted text never pass through kb_uploads."""
        db = _db(None)

        view = await kb_uploads_repo.get_view_by_artifact(db, kb_id=42, org_id=1, artifact_id="art-url")

        assert view is None
        # No second query: there is nothing to compare newness against.
        assert db.execute.await_count == 1


class TestDocumentPath:
    def test_a_first_upload_is_its_own_document_key(self) -> None:
        assert _row(artifact_id="a", source_ref="file:sha256:v1").document_path == "file:sha256:v1"

    def test_a_replacement_inherits_the_key_it_overwrites(self) -> None:
        row = _row(
            artifact_id="a",
            source_ref="file:sha256:v2",
            target_path="file:sha256:v1",
        )
        assert row.document_path == "file:sha256:v1"

    def test_the_view_agrees_with_the_row(self) -> None:
        """The read-projection must not drift from the model."""
        row = _row(artifact_id="a", source_ref="file:sha256:v2", target_path="file:sha256:v1")
        view = kb_uploads_repo._to_view(row)
        assert view.document_path == row.document_path


class TestClaimReplacementSlot:
    """The claim is check-and-take in one step; see the repo docstring."""

    @staticmethod
    def _claim_db(*, lock_acquired: bool, pending_id: object) -> AsyncMock:
        """Stub the three statements the claim issues, in order."""
        db = AsyncMock()
        lock_result = MagicMock()
        lock_result.scalar_one = MagicMock(return_value=lock_acquired)
        expire_result = MagicMock()
        pending_result = MagicMock()
        pending_result.scalar_one_or_none = MagicMock(return_value=pending_id)
        db.execute = AsyncMock(side_effect=[lock_result, expire_result, pending_result])
        return db

    @pytest.mark.asyncio
    async def test_granted_when_the_source_is_idle(self) -> None:
        db = self._claim_db(lock_acquired=True, pending_id=None)
        assert await kb_uploads_repo.claim_replacement_slot(db, kb_id=42, org_id=1, document_path="file:sha256:v1")

    @pytest.mark.asyncio
    async def test_refused_while_a_replacement_is_processing(self) -> None:
        db = self._claim_db(lock_acquired=True, pending_id=uuid.uuid4())
        assert not await kb_uploads_repo.claim_replacement_slot(db, kb_id=42, org_id=1, document_path="file:sha256:v1")

    @pytest.mark.asyncio
    async def test_refused_without_asking_when_a_competitor_holds_the_lock(self) -> None:
        """A competitor mid-claim IS the answer to "is one already running?".

        It must not wait for their docling submit to find that out, and it
        must not fall through to the row check — their row does not exist
        yet, which is exactly the race the lock closes.
        """
        db = self._claim_db(lock_acquired=False, pending_id=None)
        assert not await kb_uploads_repo.claim_replacement_slot(db, kb_id=42, org_id=1, document_path="file:sha256:v1")
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_retires_a_stalled_attempt_before_deciding(self) -> None:
        """A dead docling task must not freeze the source for good.

        Nothing else moves a row out of processing/ingesting when its task
        never goes terminal, and the user cannot clear it by hand — an
        in-flight replacement is folded into the row of the source it
        replaces, so it has no row to delete. Marking it failed also takes
        it out of list_pending, so a task that revives after the TTL cannot
        land on top of a newer replacement.
        """
        from sqlalchemy.dialects import postgresql

        db = self._claim_db(lock_acquired=True, pending_id=None)
        await kb_uploads_repo.claim_replacement_slot(db, kb_id=42, org_id=1, document_path="file:sha256:v1")

        expire = db.execute.await_args_list[1].args[0]
        sql = str(expire.compile(dialect=postgresql.dialect())).lower()
        assert sql.startswith("update kb_uploads")
        assert "updated_at <=" in sql
        cutoff = next(v for v in expire.compile().params.values() if isinstance(v, datetime))
        assert datetime.now(UTC) - cutoff >= kb_uploads_repo.REPLACEMENT_CLAIM_TTL
        # Above knowledge-ingest's 30-minute stale reaper, so a merely slow
        # replacement keeps its claim.
        assert kb_uploads_repo.REPLACEMENT_CLAIM_TTL > timedelta(minutes=30)


class TestReplacementLockKey:
    def test_the_key_is_stable_for_one_source(self) -> None:
        a = kb_uploads_repo.replacement_lock_key(42, "file:sha256:v1")
        b = kb_uploads_repo.replacement_lock_key(42, "file:sha256:v1")
        assert a == b

    def test_different_sources_get_different_keys(self) -> None:
        assert kb_uploads_repo.replacement_lock_key(42, "file:sha256:v1") != kb_uploads_repo.replacement_lock_key(
            42, "file:sha256:v2"
        )

    def test_the_same_path_in_another_kb_is_a_different_lock(self) -> None:
        """Two KBs holding identical content must not block each other."""
        assert kb_uploads_repo.replacement_lock_key(42, "file:sha256:v1") != kb_uploads_repo.replacement_lock_key(
            43, "file:sha256:v1"
        )

    def test_the_key_fits_a_postgres_bigint(self) -> None:
        key = kb_uploads_repo.replacement_lock_key(2**31, "file:sha256:" + "f" * 64)
        assert -(2**63) <= key < 2**63
