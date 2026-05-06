# SPEC-TI-008 — retrieval-api router cross-tenant centroid contamination fix

**Audit ref:** finding **B-1**
**Standards ref:** `standards.md` section 11
**Priority:** HIGH
**Status:** Ready

## Goal

Eliminate cross-tenant routing-signal-leak in retrieval-api router. Vandaag scrolt `_default_compute_centroids` Qdrant zonder `org_id` filter — centroids worden gepollueerd met andere tenants' vectors voor common labels (Notion, Confluence, etc.).

## Acceptance criteria (EARS)

- **AC-1** `_default_compute_centroids(catalog, org_id)` in `klai-retrieval-api/retrieval_api/services/router.py` — extra `org_id` parameter.
- **AC-2** Scroll-filter op line ~195 includes `FieldCondition(key="org_id", match=MatchValue(value=str(org_id)))` naast `source_label`.
- **AC-3** Caller op line ~265 (`_centroid_cache[org_id]` populate path) propageert `org_id` mee.
- **AC-4** Cache-key blijft `_centroid_cache[org_id]` — werkt al correct na fix.
- **AC-5** Test: twee orgs A en B met overlappende source_labels (beide "Notion"). Inserts één chunk per org met semantisch verschillende vectors. Bereken centroid voor A — assert dat alleen A's vector erin zit (of: centroid is dichter bij A's vector dan bij B's).

## Implementation

Single-file fix: `klai-retrieval-api/retrieval_api/services/router.py`.

```python
async def _default_compute_centroids(
    catalog: list[KBEntry],
    org_id: str,  # NEW
) -> dict[str, list[float]]:
    """Compute per-source centroids from up to 10 chunks per source.

    audit-tenant-isolation-2026-05-05 finding B-1: filter MUST include
    org_id to prevent cross-tenant centroid contamination.
    """
    centroids = {}
    for entry in catalog:
        result = await client.scroll(
            collection_name="klai_knowledge",
            scroll_filter=Filter(must=[
                FieldCondition(key="source_label", match=MatchValue(value=entry.source_label)),
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),  # NEW
            ]),
            ...
        )
        ...
```

Caller-update:
```python
centroids = await _default_compute_centroids(catalog, org_id=org_id)
```

## Tests

`test_router_centroid_org_filter.py`:
- `test_centroids_filter_by_org()` — twee orgs, vectors verschillend, assert centroids dispjunct.
- `test_centroid_cache_keyed_by_org()` — cache-keys per org gescheiden.

## Operator-step

Geen — alleen code change.

## Worktree

`klai-router-fix` — `feature/SPEC-TI-008-RETRIEVAL-ROUTER`.
