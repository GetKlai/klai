"""Operator command: apply changed knowledge-base settings to stored chunks, with no model call.

Aliases and product rules (``portal_knowledge_bases``) feed only the cheap layer
of a chunk (cheap_layer.py): the "Also known as" line, the "Linked as" line (the
anchor texts other pages use for the chunk's page, read from ``knowledge.page_links``
through link_graph.get_anchor_texts), the ``product`` label, the
``text_fingerprint``, and the dense and sparse vectors of the embedded text. This
recomputes that layer for the points whose ``cheap_layer_version`` differs from
the KB's current settings, re-embeds (TEI and the sparse sidecar on our own GPU)
only the points whose embedded text changed, and leaves the model work (summary,
context sentence, HyPE questions) as it is. ``rebuild_kb`` redoes that model
work for every document and is never the tool for a settings change.

Production runs follow docs/runbooks/kb-patch.md: a dry run, the user's yes for
that one run, ``INGEST_OK=1``, one knowledge base as a trial, then the rest.

Usage (inside the running container, or a throwaway clone of it):

    # Read-only: per KB, how many points would change per field, and the first ten.
    python -m knowledge_ingest.scripts.kb_patch --org <org_id> [--kb <kb_slug>]

    # Write them (INGEST_OK=1 is required). Safe to rerun and to stop: a
    # patched point carries the current cheap_layer_version and is not selected again.
    INGEST_OK=1 python -m knowledge_ingest.scripts.kb_patch --org <org_id> --kb <kb_slug> --execute

``--only aliases`` or ``--only products`` recomputes one part; it checks every
point in scope and writes no ``cheap_layer_version``, since the other part was
not brought up to date. The settings are read once, up front; when they cannot
be read the run stops before it writes anything.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections import Counter
from dataclasses import dataclass, field

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    DeletePayload,
    DeletePayloadOperation,
    FieldCondition,
    Filter,
    MatchValue,
    PointVectors,
    SetPayload,
    SetPayloadOperation,
    UpdateVectors,
    UpdateVectorsOperation,
)

from knowledge_ingest import (
    cheap_layer,
    embedder,
    kb_config,
    link_graph,
    pg_store,
    sparse_embedder,
)
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.qdrant_store import COLLECTION, get_client

_BATCH = 200
_EXAMPLES = 10
_FIELDS = {
    "aliases": ("context_prefix", "text_enriched"),
    "products": ("product", "text_fingerprint"),
}
_FIELDS["all"] = (*_FIELDS["aliases"], *_FIELDS["products"], "cheap_layer_version")
_PAYLOAD = [
    "source_type",
    "source_url",
    "source_ref",
    "artifact_id",
    "path",
    "text",
    *_FIELDS["all"],
]


@dataclass
class KbResult:
    selected: int = 0
    written: int = 0
    reembedded: int = 0
    changes: Counter = field(default_factory=Counter)
    examples: list[tuple] = field(default_factory=list)


async def patch_org(
    client: AsyncQdrantClient,
    org_id: str,
    kb_slug: str | None = None,
    *,
    only: str = "all",
    execute: bool = False,
) -> dict[str, KbResult]:
    """Recompute the cheap layer of every out-of-date point of the org (or one KB).

    Without ``execute`` nothing is embedded or written.
    """
    settings = await kb_config.org_kb_settings(org_id)
    if kb_slug and kb_slug not in settings:
        raise ValueError(f"knowledge base {kb_slug!r} not found for org {org_id}")
    slugs = [kb_slug] if kb_slug else sorted(settings)
    return {
        slug: await _patch_kb(client, org_id, slug, *settings[slug], only=only, execute=execute)
        for slug in slugs
    }


async def _anchors_by_url(org_id: str, kb_slug: str, points: list) -> dict[str, list[str]]:
    """The filtered anchor texts of the pages the points belong to, by source_url.

    The page text for the "already on the page" layer is the crawled markdown;
    a page without a crawl row falls back to the artifact's stored document text.
    A point without a source_url has no page, so no anchors.
    """
    artifacts = {
        p.payload["source_url"]: p.payload.get("artifact_id")
        for p in points
        if (p.payload or {}).get("source_url")
    }
    anchors: dict[str, list[str]] = {}
    if not artifacts:
        return anchors
    async with tenant_scoped_connection(org_id) as conn:
        for url, artifact_id in artifacts.items():
            text = await pg_store.get_crawled_page_markdown(conn, org_id, kb_slug, url)
            if text is None and artifact_id:
                text = await conn.fetchval(
                    "SELECT extra->>'document_text' FROM knowledge.artifacts "
                    "WHERE org_id = $1 AND kb_slug = $2 AND id = $3::uuid",
                    org_id,
                    kb_slug,
                    artifact_id,
                )
            anchors[url] = await link_graph.get_anchor_texts(conn, url, org_id, kb_slug, text or "")
    return anchors


async def _patch_kb(
    client: AsyncQdrantClient,
    org_id: str,
    kb_slug: str,
    groups: list[list[str]],
    rules: list[dict[str, str]],
    *,
    only: str,
    execute: bool,
) -> KbResult:
    fields = _FIELDS[only]
    version = cheap_layer.layer_version(groups, rules)
    selection = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
        ],
        must_not=(
            [FieldCondition(key="cheap_layer_version", match=MatchValue(value=version))]
            if only == "all"
            else None
        ),
    )
    result = KbResult()
    offset = None
    while True:
        points, offset = await client.scroll(
            COLLECTION,
            scroll_filter=selection,
            limit=_BATCH,
            offset=offset,
            with_payload=_PAYLOAD,
            with_vectors=False,
        )
        result.selected += len(points)
        updates = []  # (point id, payload to set, keys to delete, text to embed or None)
        anchors = (
            await _anchors_by_url(org_id, kb_slug, points) if "text_enriched" in fields else {}
        )
        for point in points:
            old = point.payload or {}
            new = cheap_layer.cheap_fields(
                old, groups, rules, anchors.get(old.get("source_url"), [])
            )
            # A field cheap_fields leaves out is not its to change, except a
            # product no rule matches any more: that label is deleted.
            changed = [
                f for f in fields if (f in new or f == "product") and new.get(f) != old.get(f)
            ]
            for f in changed:
                result.changes[f] += 1
                if len(result.examples) < _EXAMPLES:
                    result.examples.append((point.id, f, old.get(f), new.get(f)))
            if changed:
                updates.append(
                    (
                        point.id,
                        {f: new[f] for f in changed if f in new},
                        [f for f in changed if f not in new],
                        new["text_enriched"] if "text_enriched" in changed else None,
                    )
                )
        if execute and updates:
            await _write(client, updates)
            result.written += len(updates)
            result.reembedded += sum(1 for *_, text in updates if text is not None)
        if offset is None:
            return result


async def _write(client: AsyncQdrantClient, updates: list[tuple]) -> None:
    """Embed the changed texts, then write vectors, payloads and the version stamp, in that order.

    Qdrant applies the operations of one call in order and keeps the ones before
    a failure (a point deleted by a concurrent re-ingest fails its operation).
    Vectors go first and ``cheap_layer_version`` last, so a point never carries
    the current version, or new text, next to its old vectors: whatever a
    failure leaves behind is selected and redone by the next run.

    A sparse vector the sidecar did not return raises before anything is
    written: a new dense vector next to the old sparse one would leave the two
    retrieval legs disagreeing about the same chunk.
    """
    reembed = [(pid, text) for pid, _, _, text in updates if text is not None]
    texts = [text for _, text in reembed]
    dense = await embedder.embed(texts) if texts else []
    sparse = await sparse_embedder.embed_sparse_batch(texts) if texts else []
    if any(vector is None for vector in sparse):
        raise RuntimeError("sparse sidecar returned no vector; nothing written for this batch")
    operations: list = []
    if reembed:
        vectors = [
            PointVectors(id=pid, vector={"vector_chunk": d, "vector_sparse": s})
            for (pid, _), d, s in zip(reembed, dense, sparse, strict=True)
        ]
        operations.append(UpdateVectorsOperation(update_vectors=UpdateVectors(points=vectors)))
    stamps: dict[str, list] = {}
    for pid, payload, deleted, _ in updates:
        if version := payload.pop("cheap_layer_version", None):
            stamps.setdefault(version, []).append(pid)
        if payload:
            operations.append(
                SetPayloadOperation(set_payload=SetPayload(payload=payload, points=[pid]))
            )
        if deleted:
            operations.append(
                DeletePayloadOperation(delete_payload=DeletePayload(keys=deleted, points=[pid]))
            )
    operations += [
        SetPayloadOperation(
            set_payload=SetPayload(payload={"cheap_layer_version": version}, points=pids)
        )
        for version, pids in stamps.items()
    ]
    await client.batch_update_points(COLLECTION, update_operations=operations)


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 80 else f"…{text[-79:]}"


async def _main(args: argparse.Namespace) -> None:
    results = await patch_org(get_client(), args.org, args.kb, only=args.only, execute=args.execute)
    for slug, result in results.items():
        counts = ", ".join(f"{f} {n}" for f, n in sorted(result.changes.items())) or "no changes"
        print(f"{slug}: {result.selected} points checked; {counts}")
        for pid, f, old, new in result.examples:
            print(f"  {pid} {f}: {_short(old)} -> {_short(new)}")
        if args.execute:
            print(f"  written {result.written}, re-embedded {result.reembedded}")
    if not args.execute:
        print("dry run: nothing embedded or written")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", required=True, help="org_id (zitadel id) to patch")
    parser.add_argument("--kb", help="limit to one knowledge base slug")
    parser.add_argument("--only", choices=["aliases", "products", "all"], default="all")
    parser.add_argument("--execute", action="store_true", help="embed and write the changes")
    args = parser.parse_args()
    # The user's yes for this one run (docs/runbooks/kb-patch.md) is carried as
    # INGEST_OK=1; without it a write is refused before anything is read.
    if args.execute and os.environ.get("INGEST_OK") != "1":
        parser.exit(
            2, "kb_patch: --execute needs INGEST_OK=1, set after the user's yes for this run\n"
        )
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
