"""The anchor-text filter: which incoming link texts survive as "Linked as" vocabulary.

Synthetic data only: invented products on example.com.
"""

from __future__ import annotations

import pytest

from knowledge_ingest.anchor_filter import MAX_ANCHORS, filter_anchors

PAGE = "Orbit Desk lets you answer calls from your laptop. Install it from the download page."


def _kept(candidates: list[str], page: str = PAGE) -> list[str]:
    return filter_anchors(candidates, page)[0]


@pytest.mark.parametrize(
    ("text", "layer"),
    [
        ("   ", "empty"),
        ("https://www.example.com/orbit", "url"),
        ("www.example.com", "url"),
        ("Orbit price list.pdf", "file_extension"),
        ("orbit-desk-install", "slug"),
        ("[1]", "no_letters"),
        ("2026 - 10", "no_letters"),
        ("NL", "language_code"),
        ("en-gb", "language_code"),
        ("one two three four five six seven eight nine ten eleven twelve thirteen", "too_long"),
        ("x" * 81, "too_long"),
        ("a1 b2 c3 4 5 6 7", "few_letters"),
        ("Read more", "generic"),
        ("Klik hier", "generic"),
        ("deze pagina.", "generic"),
        ("in deze handleiding", "generic"),
        ("zie hier", "generic"),
        ("Mehr zu dieser Seite", "generic"),
        ("Mehr lesen", "generic"),
        ("the page", "generic"),
    ],
)
def test_a_text_that_is_not_a_name_is_dropped_by_its_layer(text: str, layer: str):
    kept, dropped = filter_anchors([text], PAGE)

    assert kept == []
    assert dropped == {layer: 1}


def test_generic_words_next_to_a_name_keep_the_text():
    assert _kept(["Learn more about Unite"]) == ["Learn more about Unite"]


def test_a_text_the_page_already_contains_adds_nothing_and_is_dropped():
    kept, dropped = filter_anchors(["orbit desk", "Install it"], PAGE)

    assert kept == []
    assert dropped == {"on_page": 2}


def test_the_page_comparison_ignores_case_width_and_matches_whole_words_only():
    # Fullwidth letters fold to the same text under NFKC; "desk" inside "desktop" is not a mention.
    fullwidth = "".join(chr(ord(c) + 0xFEE0) for c in "ORBIT DESK" if c != " ")
    assert _kept([f"{fullwidth[:5]} {fullwidth[5:]}"]) == []
    assert _kept(["Desktop client"], "Orbit Desk app") == ["Desktop client"]


def test_the_first_seen_spelling_wins_a_case_insensitive_duplicate():
    kept, dropped = filter_anchors(["Fax inbox", "fax INBOX", "Voicemail to email"], PAGE)

    assert kept == ["Fax inbox", "Voicemail to email"]
    assert dropped == {"duplicate": 1}


def test_at_most_five_texts_are_kept_in_the_order_given():
    names = [f"Product name {chr(ord('a') + n)}" for n in range(MAX_ANCHORS + 2)]

    kept, dropped = filter_anchors(names, PAGE)

    assert kept == names[:MAX_ANCHORS]
    assert dropped == {"over_cap": 2}
