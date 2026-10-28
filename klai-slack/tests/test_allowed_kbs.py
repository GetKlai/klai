from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services import allowed_kbs
from tests.fakes import Partner, Store


async def test_layer1_is_cached_per_link_and_refetched_after_a_relink(
    store: Store, partner: Partner
) -> None:
    first = await allowed_kbs.allowed_kbs("T1")
    await allowed_kbs.allowed_kbs("T1")
    assert len(partner.requests) == 1, "second lookup within 60 s is served from the cache"

    partner.kbs = partner.kbs[:1]
    store.link = SimpleNamespace(
        org_name="Voorbeeld BV",
        key_id=43,
        linked_at=datetime(2026, 2, 1, tzinfo=UTC),
        revoked_at=None,
    )
    second = await allowed_kbs.allowed_kbs("T1")
    assert [kb.id for kb in first] == [1, 2, 3]
    assert [kb.id for kb in second] == [1]


async def test_revoked_link_is_not_linked(store: Store, partner: Partner) -> None:
    store.link.revoked_at = datetime(2026, 3, 1, tzinfo=UTC)
    with pytest.raises(allowed_kbs.NotLinked):
        await allowed_kbs.allowed_kbs("T1")
    assert partner.requests == []
