"""The cheap layer of a chunk: what follows from its stored text and its knowledge base's settings.

A chunk carries two kinds of derived data. The model work (document summary,
context sentence, HyPE questions; enrichment.py, contextual.py) is expensive and
does not change when a setting does. The fields below follow from the stored
text and two KB settings in ``portal_knowledge_bases`` and cost no model call:

* the "Also known as" line from ``aliases`` (plan step 6), in the context prefix
  that the reranker reads and in the embedded text;
* the "Linked as" line from the filtered anchor texts of the page (anchor_filter.py),
  in the embedded text only: the reranker reads the context prefix and the text,
  so a name only other pages use must not displace the page's own words there;
* ``product`` from ``product_rules`` (product_label.py, plan step 7);
* ``text_fingerprint`` (a MinHash of the text, set once);
* ``cheap_layer_version``: a hash of the two settings, and of ANCHOR_RULES_VERSION,
  that produced the above.

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
ANCHOR_LABEL = "Linked as: "
# Bump when anchor_filter.py or the anchor line changes: it is part of the hashed
# blob, so kb_patch sees every stored chunk as stale once and rewrites its line.
ANCHOR_RULES_VERSION = 2


def layer_version(groups: list[list[str]], rules: list[dict[str, str]]) -> str:
    blob = json.dumps([groups, rules, ANCHOR_RULES_VERSION], sort_keys=True, ensure_ascii=False)
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


def _anchor_line(anchors: list[str]) -> str:
    # One line: a link text with a line break would split the embedded text.
    return ANCHOR_LABEL + " | ".join(" ".join(a.split()) for a in anchors) if anchors else ""


def _without_last_paragraph(enriched: str, label: str, text: str) -> str:
    # A paragraph the chunk's own text ends with is content, whatever it starts
    # with (a glossary entry can end in "Also known as: ..."); only a paragraph
    # appended after the text is ours to strip.
    head, sep, last = enriched.rpartition("\n\n")
    if sep and last.startswith(label) and not text.endswith(last):
        return head
    return enriched


def _without_derived_lines(context_prefix: str, text_enriched: str, text: str) -> tuple[str, str]:
    """Strip the lines a previous run added, from the end of both fields.

    The anchor line is the last line of the embedded text only. The alias line
    is the last line of the context prefix and the end of the embedded text
    before that. Before the anchor line had its own label, ingest appended the
    raw anchor texts as "Also known as: ..." to the embedded text alone: such a
    line without a twin at the end of the prefix is that old block, and goes too.
    """
    enriched = _without_last_paragraph(text_enriched, ANCHOR_LABEL, text)
    head, _, last = context_prefix.rpartition("\n")
    if last.startswith(ALIAS_LABEL) and enriched.endswith(f"\n\n{last}"):
        context_prefix, enriched = head, enriched[: -len(last) - 2]
    return context_prefix, _without_last_paragraph(enriched, ALIAS_LABEL, text)


def cheap_fields(
    payload: Mapping[str, Any],
    groups: list[list[str]],
    rules: list[dict[str, str]],
    anchors: list[str],
) -> dict[str, str]:
    """The cheap fields for one chunk payload, given its KB's alias groups and product rules.

    ``product`` is absent when no rule matches. ``text_fingerprint`` is returned
    only when the payload has none. ``context_prefix`` and ``text_enriched`` are
    returned only for an enriched chunk; ``text_enriched`` is the text to embed
    and ends with the alias line and then the anchor line, when there are any.
    """
    fields = {"cheap_layer_version": layer_version(groups, rules)}
    if product := derive_product(payload, rules):
        fields["product"] = product
    if not payload.get("text_fingerprint") and (
        fingerprint := compute_minhash(payload.get("text") or "")
    ):
        fields["text_fingerprint"] = fingerprint
    if "text_enriched" in payload:
        text = payload.get("text") or ""
        prefix, enriched = _without_derived_lines(
            payload.get("context_prefix") or "", payload["text_enriched"], text
        )
        if line := _alias_line(text, groups):
            prefix = f"{prefix}\n{line}".strip()
            enriched += f"\n\n{line}"
        if line := _anchor_line(anchors):
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
    anchors = extra_payload.get("anchor_texts") or []
    extra_payload.update(cheap_fields({**extra_payload, "path": path}, groups, rules, anchors))
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
            anchors,
        )
        chunk.context_prefix, chunk.enriched_text = (
            fields["context_prefix"],
            fields["text_enriched"],
        )
