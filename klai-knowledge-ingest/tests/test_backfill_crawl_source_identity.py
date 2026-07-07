from typing import Any

import pytest

from knowledge_ingest.scripts import backfill_crawl_source_identity as backfill
from knowledge_ingest.scripts.backfill_crawl_source_identity import (
    cleaned_taxonomy_node_ids,
    run_backfill,
    source_identity_payload,
)


def test_source_identity_payload_derives_domain_and_label_from_source_url() -> None:
    payload = source_identity_payload({"source_url": "https://wiki.example.cloud/nl/pagina"})
    assert payload == {
        "source_domain": "wiki.example.cloud",
        "source_label": "wiki.example.cloud",
    }


def test_source_identity_payload_ignores_invalid_source_url() -> None:
    assert source_identity_payload({"source_url": "not-a-url"}) == {}


def test_cleaned_taxonomy_node_ids_removes_stale_ids() -> None:
    assert cleaned_taxonomy_node_ids(
        {"taxonomy_node_ids": [1, 2, 99]},
        {1, 2},
    ) == [1, 2]


def test_cleaned_taxonomy_node_ids_returns_none_when_byte_identical() -> None:
    assert (
        cleaned_taxonomy_node_ids(
            {"taxonomy_node_ids": [1, 2]},
            {1, 2, 3},
        )
        is None
    )


class _FakePoint:
    def __init__(self, point_id: str, payload: dict[str, Any]) -> None:
        self.id = point_id
        self.payload = payload


class _FakeQdrantClient:
    """In-memory scroll/set_payload double for the two backfill passes."""

    def __init__(self, points: dict[str, dict[str, Any]]) -> None:
        self.points = points
        self.set_payload_calls: list[dict[str, Any]] = []

    @staticmethod
    def _matches(scroll_filter: Any, payload: dict[str, Any]) -> bool:
        for condition in scroll_filter.must or []:
            if hasattr(condition, "key") and hasattr(condition, "match"):
                if payload.get(condition.key) != condition.match.value:
                    return False
            elif hasattr(condition, "is_empty"):
                if payload.get(condition.is_empty.key):
                    return False
        for condition in scroll_filter.must_not or []:
            if hasattr(condition, "is_empty") and not payload.get(condition.is_empty.key):
                return False
        return True

    async def scroll(
        self, *, collection_name, scroll_filter, limit, offset, with_payload, with_vectors
    ):
        if offset is not None:
            # Single-page fake: a second page is always empty.
            return [], None
        matched = [
            _FakePoint(point_id, dict(payload))
            for point_id, payload in self.points.items()
            if self._matches(scroll_filter, payload)
        ]
        return matched, "next" if matched else None

    async def set_payload(self, *, collection_name, payload, points):
        self.set_payload_calls.append({"payload": payload, "points": list(points)})
        for point_id in points:
            self.points[point_id].update(payload)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch):
    client = _FakeQdrantClient(
        {
            # Group (a): crawl chunk missing source_domain, with taxonomy ids.
            "a1": {
                "source_type": "crawl",
                "source_url": "https://wiki.example.cloud/nl/pagina",
                "taxonomy_node_ids": [1, 99],
                "kb_slug": "support",
            },
            # Second group-(a) chunk on the same domain (batching check).
            "a2": {
                "source_type": "crawl",
                "source_url": "https://wiki.example.cloud/nl/andere",
                "kb_slug": "support",
            },
            # Group (b): stale taxonomy id on a chunk that ALREADY has a
            # source_domain — must still be cleaned (Scenario 10 group b).
            "b1": {
                "source_type": "crawl",
                "source_url": "https://help.example.nl/x",
                "source_domain": "help.example.nl",
                "source_label": "help.example.nl",
                "taxonomy_node_ids": [2, 99],
                "kb_slug": "support",
            },
            # Group (c): already correct — must stay byte-identical.
            "c1": {
                "source_type": "crawl",
                "source_url": "https://help.example.nl/y",
                "source_domain": "help.example.nl",
                "source_label": "help.example.nl",
                "taxonomy_node_ids": [1, 2],
                "kb_slug": "support",
            },
        }
    )
    monkeypatch.setattr(backfill.qdrant_store, "get_client", lambda: client)

    async def _fake_valid_ids() -> set[int]:
        return {1, 2}

    monkeypatch.setattr(backfill, "_valid_taxonomy_node_ids", _fake_valid_ids)
    return client


@pytest.mark.asyncio
async def test_apply_without_clean_flag_never_touches_taxonomy(fake_client) -> None:
    """Regression: a plain --apply run must not wipe taxonomy_node_ids."""
    report = await run_backfill(apply=True, clean_stale_taxonomy=False, batch_size=10)

    assert fake_client.points["a1"]["taxonomy_node_ids"] == [1, 99]
    assert fake_client.points["b1"]["taxonomy_node_ids"] == [2, 99]
    assert all("taxonomy_node_ids" not in call["payload"] for call in fake_client.set_payload_calls)
    assert report["stale_taxonomy_cleaned_by_kb"] == {}
    assert report["source_domain_counts"] == {"wiki.example.cloud": 2}


@pytest.mark.asyncio
async def test_source_identity_updates_are_batched_per_domain(fake_client) -> None:
    await run_backfill(apply=True, clean_stale_taxonomy=False, batch_size=10)

    domain_calls = [
        call for call in fake_client.set_payload_calls if "source_domain" in call["payload"]
    ]
    assert len(domain_calls) == 1
    assert sorted(domain_calls[0]["points"]) == ["a1", "a2"]
    assert fake_client.points["a1"]["source_domain"] == "wiki.example.cloud"
    assert fake_client.points["a2"]["source_label"] == "wiki.example.cloud"


@pytest.mark.asyncio
async def test_clean_stale_taxonomy_also_visits_chunks_with_source_domain(fake_client) -> None:
    """Scenario 10 group (b): stale ids are cleaned even when source_domain is set."""
    report = await run_backfill(apply=True, clean_stale_taxonomy=True, batch_size=10)

    assert fake_client.points["b1"]["taxonomy_node_ids"] == [2]
    assert fake_client.points["a1"]["taxonomy_node_ids"] == [1]
    assert fake_client.points["c1"]["taxonomy_node_ids"] == [1, 2]
    assert report["stale_taxonomy_cleaned_by_kb"] == {"support": 2}


@pytest.mark.asyncio
async def test_second_run_reports_zero_mutations(fake_client) -> None:
    await run_backfill(apply=True, clean_stale_taxonomy=True, batch_size=10)
    before = {pid: dict(p) for pid, p in fake_client.points.items()}

    report = await run_backfill(apply=True, clean_stale_taxonomy=True, batch_size=10)

    assert report["mutated"] == 0
    assert fake_client.points == before


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(fake_client) -> None:
    before = {pid: dict(p) for pid, p in fake_client.points.items()}

    report = await run_backfill(apply=False, clean_stale_taxonomy=True, batch_size=10)

    assert report["dry_run"] is True
    assert fake_client.set_payload_calls == []
    assert fake_client.points == before
    assert report["source_domain_counts"] == {"wiki.example.cloud": 2}
    assert report["stale_taxonomy_cleaned_by_kb"] == {"support": 2}
