from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api import app_product_updates
from app.api.admin import platform_product_updates
from app.models.product_updates import ProductUpdate, ProductUpdateRead
from app.product_updates import service


class _Result:
    def __init__(self, rows=None, *, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._rows[0]


class _ListSession:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return _Result(self.rows)


class _ReadSession:
    def __init__(self, read):
        self.read = read
        self.statements = []
        self.commits = 0

    async def scalar(self, _query):
        return 1

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _Result(rowcount=1)
        return _Result([self.read])

    async def commit(self):
        self.commits += 1


class _MarkAllSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.commits = 0

    async def execute(self, statement):
        self.calls += 1
        if self.calls == 1:
            return _Result(self.rows)
        return _Result(rowcount=1)

    async def commit(self):
        self.commits += 1


class _CreateSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    def add(self, row):
        self.added.append(row)

    async def scalar(self, _query):
        return self.existing

    async def flush(self):
        for row in self.added:
            if isinstance(row, ProductUpdate):
                row.id = 123
                if row.created_at is None:
                    row.created_at = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
                row.published_at = datetime(2026, 6, 6, 10, 1, tzinfo=UTC)

    async def commit(self):
        self.commits += 1


def test_normalize_commit_shas_dedupes_and_lowercases():
    assert service.normalize_commit_shas(["ABC1234", "abc1234", "", "def5678"]) == ["abc1234", "def5678"]


def test_normalize_commit_shas_rejects_non_sha_values():
    with pytest.raises(service.ProductUpdateValidationError):
        service.normalize_commit_shas(["not-a-sha"])


def test_normalize_dedupe_key_allows_operator_safe_keys():
    assert service.normalize_dedupe_key("  release:2026-06-06:sources  ") == "release:2026-06-06:sources"


def test_normalize_dedupe_key_rejects_unsafe_values():
    with pytest.raises(service.ProductUpdateValidationError):
        service.normalize_dedupe_key("not a key")


def test_generated_dedupe_key_is_stable_for_same_payload():
    first = service.generated_dedupe_key(title="Title", body="Body", commit_shas=["abc1234"])
    second = service.generated_dedupe_key(title="Title", body="Body", commit_shas=["abc1234"])

    assert first == second
    assert first.startswith("sha256:")


@pytest.mark.asyncio
async def test_product_updates_list_is_scoped_to_current_user():
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    update = ProductUpdate(id=1, title="A fix shipped", body="Reports now show their latest status.", commit_shas=[])
    update.created_at = now
    session = _ListSession([(update, None)])

    rows = await service.list_product_updates_for_user(session, org_id=42, user_id="user-123", limit=500)

    assert rows == [(update, None)]
    compiled = str(session.queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert "product_update_reads.org_id = 42" in compiled
    assert "product_update_reads.user_id = 'user-123'" in compiled
    assert "LIMIT 100" in compiled


@pytest.mark.asyncio
async def test_app_product_updates_response_includes_unread_count():
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    update = ProductUpdate(
        id=1,
        title="A fix shipped",
        body="Reports now show their latest status.",
        commit_shas=["abc1234"],
    )
    update.created_at = now
    session = _ListSession([(update, None)])

    result = await app_product_updates.get_product_updates(
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.unread_count == 1
    assert result.items[0].title == "A fix shipped"
    assert result.items[0].commit_shas == ["abc1234"]
    assert result.items[0].unread is True


@pytest.mark.asyncio
async def test_app_product_update_can_mark_one_update_read():
    read_at = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    read = ProductUpdateRead(product_update_id=1, org_id=42, user_id="user-123", read_at=read_at)
    session = _ReadSession(read)

    result = await app_product_updates.mark_product_update_read_endpoint(
        1,
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.product_update_id == 1
    assert result.read_at == read_at
    assert session.commits == 1


@pytest.mark.asyncio
async def test_app_product_updates_can_mark_all_unread_updates_read():
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    unread = ProductUpdate(id=1, title="A fix shipped", body="Reports now show their latest status.", commit_shas=[])
    unread.created_at = now
    read = ProductUpdate(id=2, title="Already read", body="This update was read.", commit_shas=[])
    read.created_at = now
    session = _MarkAllSession([(unread, None), (read, now)])

    result = await app_product_updates.mark_all_product_updates_read_endpoint(
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.read_count == 1
    assert session.commits == 1


@pytest.mark.asyncio
async def test_platform_admin_can_create_product_update(monkeypatch):
    session = _CreateSession()
    monkeypatch.setattr(platform_product_updates, "cross_org_session", lambda: session)
    released_at = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)

    result = await platform_product_updates.create_product_update_endpoint(
        platform_product_updates.ProductUpdateCreateIn(
            title="Feedback updates are easier to follow",
            body="You can now see what happened with a report directly from your account menu.",
            commit_shas=["ABC1234", "abc1234"],
            created_at=released_at,
            dedupe_key="release:feedback",
        ),
        _perms=SimpleNamespace(user_id="admin", org_id=1),
    )

    assert result.id == 123
    assert result.commit_shas == ["abc1234"]
    assert result.created_at == released_at
    assert result.created_by_user_id == "admin"
    assert result.dedupe_key == "release:feedback"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_create_product_update_is_idempotent_by_dedupe_key():
    existing = ProductUpdate(
        id=77,
        title="Existing update",
        body="Already published.",
        commit_shas=["abc1234"],
        dedupe_key="release:existing",
        created_by_user_id="admin",
        published_via="admin_api",
    )
    existing.created_at = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    existing.published_at = datetime(2026, 6, 6, 10, 1, tzinfo=UTC)
    session = _CreateSession(existing=existing)

    result = await service.create_product_update(
        session,
        title="Ignored duplicate",
        body="Ignored duplicate body.",
        commit_shas=["def5678"],
        dedupe_key="release:existing",
        created_by_user_id="other-admin",
    )

    assert result is existing
    assert session.added == []
