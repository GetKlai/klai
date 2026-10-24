"""The cheap layer of a chunk: what follows from its stored text and its knowledge base's settings.

A chunk carries two kinds of derived data. The model work (document summary,
context sentence, HyPE questions; enrichment.py, contextual.py) is expensive and
does not change when a setting does. The fields below follow from the stored
text and two KB settings in ``portal_knowledge_bases`` and cost no model call:

* the "Also known as" line from ``aliases`` (plan step 6), in the context prefix
  that the reranker reads and in the embedded text;
* ``product`` from ``product_rules`` (product_label.py, plan step 7);
* ``text_fingerprint`` (a MinHash of the text, set once);
* ``cheap_layer_version``: a hash of the two settings that produced the above.

The ingest path and ``scripts/kb_patch.py`` both call :func:`cheap_fields`, so a
new document and a patched old one cannot drift. A changed setting is applied to
existing chunks by that patch, never by redoing the model work (``rebuild_kb``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from knowledge_ingest.product_label import derive_product
from knowledge_ingest.utils.content_fingerprint import compute_minhash

if TYPE_CHECKING:
    from knowledge_ingest.enrichment import EnrichedChunk

ALIAS_LABEL = "Also known as: "


def layer_version(groups: list[list[str]], rules: list[dict[str, str]]) -> str:
    blob = json.dumps([groups, rules], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# The alias matching is duplicated in retrieval-api's search service (query
# expansion); the two are separate deployables with no shared runtime package.
def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def _alias_line(text: str, groups: list[list[str]]) -> str:
    names: list[str] = []
    for group in groups:
        if any(_mentions(text, term) for term in group):
            names += [t for t in group if t not in names and not _mentions(text, t)]
    return ALIAS_LABEL + " | ".join(names) if names else ""


def _without_alias_line(context_prefix: str, text_enriched: str) -> tuple[str, str]:
    """Strip the alias line a previous run added.

    It is the last line of the context prefix and the end of the embedded text.
    Anchor texts (enrichment_tasks) use the same label but only in the embedded
    text, so requiring both keeps them.
    """
    head, _, last = context_prefix.rpartition("\n")
    if last.startswith(ALIAS_LABEL) and text_enriched.endswith(f"\n\n{last}"):
        return head, text_enriched[: -len(last) - 2]
    return context_prefix, text_enriched


def cheap_fields(
    payload: Mapping[str, Any], groups: list[list[str]], rules: list[dict[str, str]]
) -> dict[str, str]:
    """The cheap fields for one chunk payload, given its KB's alias groups and product rules.

    ``product`` is absent when no rule matches. ``text_fingerprint`` is returned
    only when the payload has none. ``context_prefix`` and ``text_enriched`` are
    returned only for an enriched chunk; ``text_enriched`` is the text to embed.
    """
    fields = {"cheap_layer_version": layer_version(groups, rules)}
    if product := derive_product(payload, rules):
        fields["product"] = product
    if not payload.get("text_fingerprint") and (
        fingerprint := compute_minhash(payload.get("text") or "")
    ):
        fields["text_fingerprint"] = fingerprint
    if "text_enriched" in payload:
        prefix, enriched = _without_alias_line(
            payload.get("context_prefix") or "", payload["text_enriched"]
        )
        if line := _alias_line(payload.get("text") or "", groups):
            prefix = f"{prefix}\n{line}".strip()
            enriched += f"\n\n{line}"
        fields["context_prefix"], fields["text_enriched"] = prefix, enriched
    return fields


def apply_to_enriched_chunks(
    chunks: list[EnrichedChunk],
    extra_payload: dict,
    path: str,
    groups: list[list[str]],
    rules: list[dict[str, str]],
) -> None:
    """The ingest side: give freshly enriched chunks the fields kb_patch would write."""
    extra_payload.pop("product", None)
    extra_payload.update(cheap_fields({**extra_payload, "path": path}, groups, rules))
    for chunk in chunks:
        fields = cheap_fields(
            {
                **extra_payload,
                "path": path,
                "text": chunk.original_text,
                "context_prefix": chunk.context_prefix,
                "text_enriched": chunk.enriched_text,
            },
            groups,
            rules,
        )
        chunk.context_prefix, chunk.enriched_text = (
            fields["context_prefix"],
            fields["text_enriched"],
        )
