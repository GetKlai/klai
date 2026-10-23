"""The product a document is about, stored per chunk as ``product`` (knowledge-base plan, step 7).

Help centres publish near-identical articles per product (same template,
another product name), and in the September baseline a sixth of the wrongly
judged chat turns answered from such a lookalike. The label lets retrieval-api
prefer the chunks of the product a question is about.

A knowledge base's ``product_rules`` (portal_knowledge_bases.product_rules)
come first: the first rule whose ``prefix`` starts the document's host plus
URL path (crawled pages) or its path (every other source) gives the label.
Without a matching rule a crawled page takes the first segment of a nested URL
path and a connector document the folder or space its path starts with; a page
directly under the root or a flat path names itself, not a product, and gets
no label. An empty label means "unknown", never "no product".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

_URL_SOURCES = frozenset({"crawl", "url"})


def derive_product(payload: Mapping[str, Any], rules: list[dict[str, str]]) -> str:
    source_type = payload.get("source_type") or ""
    if source_type in _URL_SOURCES:
        url = urlsplit(str(payload.get("source_url") or payload.get("source_ref") or ""))
        path = url.path
        target = f"{url.hostname or ''}{path}"
    else:
        path = target = str(payload.get("path") or "")
    for rule in rules:
        if target.casefold().startswith(rule["prefix"].casefold()):
            return rule["product"]
    if source_type not in _URL_SOURCES and source_type != "connector":
        return ""
    segments = [segment for segment in path.split("/") if segment]
    return segments[0] if len(segments) > 1 else ""
