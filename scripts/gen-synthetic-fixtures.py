#!/usr/bin/env python3
"""Generate the synthetic wiki-page fixtures of klai-knowledge-ingest.

The login-wall tests need pages shaped like a help-centre wiki: a shared
template (availability lines, tab row, feature list, parameter list) around
page-specific sections, and on the walled page two protected FAQ tabs that
each show a login link. Earlier versions of these fixtures were copies of a
real customer wiki; the repository is public, so the text here is invented
by a seeded generator that never reads any source page. Only the shape
follows the original: heading count, list sizes, links and word count of the
same order.

Run from the repository root:  python3 scripts/gen-synthetic-fixtures.py
The output is deterministic: rerunning it must leave git clean.
"""

from __future__ import annotations

import random
from pathlib import Path

GENERATOR = "scripts/gen-synthetic-fixtures.py"
OUT = Path(__file__).resolve().parents[1] / "klai-knowledge-ingest" / "tests" / "fixtures"
HOST = "https://wiki.example.com"
# The template every page of the wiki shares. One seed for all pages, so the
# pages really do share it, as pages of one CMS do.
TEMPLATE_SEED = 7

_ONSETS = ["b", "d", "f", "g", "k", "l", "m", "n", "p", "r", "s", "t", "v", "w", "z", "st", "br", "kl", "tr", "sl"]
_VOWELS = ["a", "e", "i", "o", "u", "aa", "ee", "oo", "ie", "ei", "ou"]
_CODAS = ["", "", "k", "l", "m", "n", "r", "s", "t", "nd", "rt", "ng"]
_GLUE = ["de", "het", "een", "en", "je", "van", "in", "op", "met", "voor", "naar", "dat", "is", "kun", "om", "bij"]


def _word(rng: random.Random) -> str:
    return "".join(rng.choice(_ONSETS) + rng.choice(_VOWELS) + rng.choice(_CODAS) for _ in range(rng.randint(1, 3)))


def _pick(rng: random.Random, topic: list[str]) -> str:
    roll = rng.random()
    if roll < 0.2:
        return rng.choice(_GLUE)
    if topic and roll < 0.45:
        return rng.choice(topic)
    return _word(rng)


def _sentence(rng: random.Random, low: int = 8, high: int = 18, topic: list[str] | None = None) -> str:
    words = [_pick(rng, topic or []) for _ in range(rng.randint(low, high))]
    return words[0].capitalize() + " " + " ".join(words[1:]) + "."


def _paragraph(rng: random.Random, sentences: int, topic: list[str] | None = None) -> str:
    return " ".join(_sentence(rng, topic=topic) for _ in range(sentences))


def _title(rng: random.Random, words: int = 3) -> str:
    return " ".join(_word(rng) for _ in range(words)).capitalize()


def _field(rng: random.Random, prefix: str) -> str:
    return prefix + "_" + "_".join(_word(rng) for _ in range(rng.randint(1, 4)))


def _template() -> dict[str, list[str]]:
    rng = random.Random(TEMPLATE_SEED)
    topic = [_word(rng) for _ in range(6)]
    availability = ["Beschikbaar voor: "] + [f"{_title(rng, 2)} beschikbaar voor: {_title(rng, 1)} " for _ in range(4)]
    features: list[str] = []
    for _ in range(5):
        features += ["", _title(rng, 2)]
        for _ in range(rng.randint(2, 6)):
            features += [f"  * {_title(rng, rng.randint(1, 4))}", _paragraph(rng, rng.randint(1, 2), topic) + " "]
    standard = ["", _title(rng, 3)]
    for _ in range(16):
        standard += [f"  * {_title(rng, rng.randint(1, 3))}", _sentence(rng, 8, 16, topic) + " "]
    parameters = [f"  * {_field(rng, 'number')}" for _ in range(9)]
    parameters += [f"  * {_field(rng, 'call')}" for _ in range(17)]
    return {"availability": availability, "features": features, "standard": standard, "parameters": parameters}


def _tab_row(slug: str, rng: random.Random, tabs: int) -> str:
    names = ["Instructies", "Kenmerken", "Kennisbank", "Veelgestelde vragen", "Parameters", "Knoppen"][:tabs]
    return " ".join(f"[{name}]({HOST}/nl/{slug}#tab-{_word(rng)})" for name in names)


def _config_sections(rng: random.Random, count: int, links: int, slug: str, topic: list[str]) -> list[str]:
    lines: list[str] = []
    for i in range(count):
        lines += [f"## {_title(rng, rng.randint(2, 4))}", _paragraph(rng, rng.randint(3, 7), topic)]
        if i < links:
            lines.append(
                f"{_sentence(rng, topic=topic)} Zie [{_title(rng, 1)}]({HOST}/nl/{slug}/{_word(rng)}) {_sentence(rng, topic=topic)}"
            )
    return lines


def _faq(rng: random.Random, slug: str, sections: int, gated: int, topic: list[str]) -> list[str]:
    titles = [_title(rng, rng.randint(3, 6)) for _ in range(sections)]
    lines = ["", "Selected article: ", " ".join(titles)]
    lines.append(" ".join(f"[ {t}]({HOST}/nl/{slug}#tab-faq-{rng.randrange(10**8):08d})" for t in titles))
    for index, title in enumerate(titles):
        lines += [f"### {title}", _paragraph(rng, rng.randint(4, 8), topic)]
        if index < gated:
            # The gate the classifier must recognise: a login link with a
            # read-this-article directive in the same short block.
            redirect = f"http%3A%2F%2Fwiki.example.com%2Fnl%2F{slug.replace('/', '%2F')}"
            lines += [
                f"##  [Log in]({HOST}/login?redirect_to={redirect})",
                ("This tab is only for licensed users of the software. Log in when you want to read this article."),
            ]
        else:
            lines += [f"  * {_title(rng, rng.randint(1, 3))}" for _ in range(rng.randint(0, 7))]
    return lines


def _page(
    seed: int, product: str, slug: str, *, configs: int, config_links: int, own_params: int, faq: int, gated: int
) -> str:
    rng = random.Random(seed)
    topic = [product.lower()] + [_word(rng) for _ in range(5)]
    template = _template()
    lines = [f"<!-- synthetic-data: generator={GENERATOR} seed={seed} -->", f"##  Handleiding - {product} "]
    lines += template["availability"]
    lines.append(_tab_row(slug, rng, 6))
    lines.append(f"# {product} {_title(rng, 1).lower()}")
    lines += _config_sections(rng, configs, config_links, slug, topic)
    lines += ["## Kenmerken", *template["features"], *template["standard"]]
    if own_params:
        lines += ["", "## Knoppen", _paragraph(rng, 2, topic)]
        lines += [f"  * {_title(rng, rng.randint(2, 4))}" for _ in range(13)]
    lines += ["", "## Parameters", _paragraph(rng, 2, topic), *template["parameters"]]
    lines += [f"  * {_field(rng, 'crm')}" for _ in range(own_params)]
    lines += _faq(rng, slug, faq, gated, topic)
    return "\n".join(lines) + "\n"


FIXTURES = {
    # Walled page: long parameter list, two protected FAQ tabs.
    "auth_walls/partner_wiki_hubspot.md": {
        "seed": 101,
        "product": "HubSpot",
        "slug": "crm-software/HubSpot",
        "configs": 6,
        "config_links": 1,
        "own_params": 340,
        "faq": 6,
        "gated": 2,
    },
    # Clean pages of the same wiki: same template, no protected tab.
    "clean_pages/partner_wiki_ifttt.md": {
        "seed": 102,
        "product": "IFTTT",
        "slug": "crm-software/IFTTT",
        "configs": 1,
        "config_links": 0,
        "own_params": 0,
        "faq": 0,
        "gated": 0,
    },
    "clean_pages/partner_wiki_zoom_setup_nl.md": {
        "seed": 103,
        "product": "Zoom",
        "slug": "phone-software/Zoom",
        "configs": 3,
        "config_links": 3,
        "own_params": 0,
        "faq": 2,
        "gated": 0,
    },
}


def main() -> None:
    for rel, spec in FIXTURES.items():
        (OUT / rel).write_text(_page(**spec), encoding="utf-8")


if __name__ == "__main__":
    main()
