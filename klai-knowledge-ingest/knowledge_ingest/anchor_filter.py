"""Which incoming link texts are worth embedding as "Linked as" vocabulary.

The crawler stores the text of every link that points at a page. Measured on one
production organisation (29 Oct 2026), most of it is navigation boilerplate
("nl", "learn more", "get a quote", "skip to main content", "[1]") and 37% of
all link texts are literally the target page's own title. An anchor is only
useful when it adds vocabulary the page does not already have, so this module
drops what is structurally not a name (the layers below) and what the page
already says. Self-links and texts that point at many pages are removed in SQL
(link_graph.get_anchor_texts), where the counting is cheap.

Two knowledge bases, simulated before building: 3,757 (target, text) pairs kept
56 on 53 pages (help centre); 2,068 kept 618 (second site). The layers that
removed most: already on the page (588 / 797 pairs), self-links (423 / 276),
too long (245 on the second site), then URL or slug, spread and language codes.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from knowledge_ingest.cheap_layer import _mentions

MAX_ANCHORS = 5
# A text pointing at this many distinct pages of one KB is a menu or footer
# label, not a name for one page.
SPREAD_LIMIT = 10
# Longer than a name: sentence-like link texts ("Read how our customers ...")
# that the second site produced 245 of.
MAX_WORDS = 12
MAX_CHARS = 80

# One small universal set, multilingual, not per customer: a text made only of
# these says "a link" and nothing about the target ("read more", "klik hier"),
# while "learn more about unite" keeps its product name.
# After the first patch run on a Dutch help centre (29 Oct 2026) the most
# frequent kept names were "deze pagina", "deze link", "in deze handleiding"
# and "zie hier": the list lacked the Dutch, German and French function words
# and the nouns that only say "a page" or "a manual".
GENERIC_WORDS = frozenset(
    "read learn more here click info information details see view page link the a an "
    "this that these those in on at to of for and or about article manual guide website "
    "lees meer hier klik bekijk zie kijk ga de het een deze dit die dat op naar van voor en "
    "of over pagina artikel handleiding website hieronder hierboven verder "
    "mehr lesen weiter hier sehen diese dieser dieses seite artikel anleitung zu zur zum "
    "auf der das den dem ein eine und oder "
    "voir plus ici cette ce cet page article guide le la les un une des du et ou sur".split()
)

_URL = re.compile(r"^(https?://|www\.)")
_EXTENSION = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|zip|png|jpe?g|gif|svg|webp|mp[34]|csv|txt|html?|php)$"
)
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
# Language switchers ("nl", "en-gb"): 360 of the 361 rows for "nl" on one site.
_LANGUAGE_CODE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")


def normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _structural_layer(norm: str) -> str | None:
    """The name of the first layer that rejects this normalised text, or None."""
    if not norm:
        return "empty"
    if _URL.match(norm):
        return "url"
    if _EXTENSION.search(norm):
        return "file_extension"
    if _LANGUAGE_CODE.match(norm):
        return "language_code"
    if _SLUG.match(norm):
        return "slug"
    if not any(c.isalpha() for c in norm):
        return "no_letters"
    if len(norm.split()) > MAX_WORDS or len(norm) > MAX_CHARS:
        return "too_long"
    visible = [c for c in norm if not c.isspace()]
    if sum(c.isalpha() for c in visible) * 2 < len(visible):
        return "few_letters"
    if all(word in GENERIC_WORDS for word in re.findall(r"\w+", norm)):
        return "generic"
    return None


def filter_anchors(candidates: Iterable[str], page_text: str) -> tuple[list[str], Counter]:
    """Up to MAX_ANCHORS of ``candidates`` (best first), plus how many each layer dropped.

    The first-seen spelling of a text wins; a text the page already contains
    (case-insensitive, whole words) is dropped as the last layer.
    """
    page = normalise(page_text)
    seen: set[str] = set()
    kept: list[str] = []
    dropped: Counter = Counter()
    for text in candidates:
        norm = normalise(text)
        if norm in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(norm)
        if layer := _structural_layer(norm):
            dropped[layer] += 1
        elif _mentions(page, norm):
            dropped["on_page"] += 1
        else:
            kept.append(text.strip())
    if len(kept) > MAX_ANCHORS:
        dropped["over_cap"] = len(kept) - MAX_ANCHORS
    return kept[:MAX_ANCHORS], dropped
