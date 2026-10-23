"""The product a document is about, stored per chunk as ``product`` (knowledge-base plan, step 7).

Help centres publish near-identical articles per product (same template,
another product name), and in the September baseline a sixth of the wrongly
judged chat turns answered from such a lookalike. The label lets retrieval-api
prefer the chunks of the product a question is about.

Only a knowledge base's ``product_rules`` (portal_knowledge_bases.product_rules)
name a product: the first rule whose ``prefix`` starts the document's host plus
URL path (crawled pages) or its path (every other source) gives the label.
Without a matching rule there is no label. A first URL segment or folder is a
language or section code (/nl/, /en/, /support/, /hc/) as often as a product,
and such a label, matched as a word in a question, told the selection step that
every correct passage was about another product.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

_URL_SOURCES = frozenset({"crawl", "url"})


def derive_product(payload: Mapping[str, Any], rules: list[dict[str, str]]) -> str:
    if payload.get("source_type") in _URL_SOURCES:
        url = urlsplit(str(payload.get("source_url") or payload.get("source_ref") or ""))
        target = f"{url.hostname or ''}{url.path}"
    else:
        target = str(payload.get("path") or "")
    return next(
        (
            rule["product"]
            for rule in rules
            if target.casefold().startswith(rule["prefix"].casefold())
        ),
        "",
    )
