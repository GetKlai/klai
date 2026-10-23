"""The chat gets a bounded window around the hit, not the whole parent block."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from retrieval_api.api.retrieve import _run_response_build
from retrieval_api.services.parent_lookup import PARENT_WINDOW_CHARS, parent_window

HEADING = "Billing > Invoices"
# Ten paragraphs of ~600 chars (a 6000-char parent), each starting with its own number.
PARAGRAPHS = [
    f"Paragraph {i:02d} " + ("explains one invoice step. " * 22).strip() for i in range(10)
]
PARENT = f"{HEADING}\n\n" + "\n\n".join(PARAGRAPHS)


def _child(index: int) -> str:
    """Child text as the chunker stores it: heading prefix plus the paragraph."""
    return f"{HEADING}\n\n{PARAGRAPHS[index]}"


def test_short_parent_is_returned_whole() -> None:
    parent = f"{HEADING}\n\n{PARAGRAPHS[0]}\n\n{PARAGRAPHS[1]}"
    assert len(parent) <= PARENT_WINDOW_CHARS

    assert parent_window(parent, _child(1), HEADING) == parent


@pytest.mark.parametrize("hit", [0, 4, 9], ids=["start", "middle", "end"])
def test_window_holds_the_hit_and_stays_under_the_cap(hit: int) -> None:
    window = parent_window(PARENT, _child(hit), HEADING)

    assert len(PARENT) > PARENT_WINDOW_CHARS
    assert len(window) <= PARENT_WINDOW_CHARS
    assert PARAGRAPHS[hit] in window
    assert window in PARENT


def test_window_at_the_start_of_the_parent_keeps_the_heading_and_drops_the_tail() -> None:
    window = parent_window(PARENT, _child(0), HEADING)

    assert window.startswith(HEADING)
    assert PARAGRAPHS[1] in window
    assert PARAGRAPHS[9] not in window


def test_window_in_the_middle_has_neighbours_on_both_sides() -> None:
    window = parent_window(PARENT, _child(5), HEADING)

    assert PARAGRAPHS[4] in window
    assert PARAGRAPHS[6] in window
    assert PARAGRAPHS[0] not in window
    assert PARAGRAPHS[9] not in window


def test_window_at_the_end_of_the_parent_reaches_the_last_paragraph() -> None:
    window = parent_window(PARENT, _child(9), HEADING)

    assert window.endswith(PARAGRAPHS[9])
    assert PARAGRAPHS[8] in window
    assert PARAGRAPHS[0] not in window


def test_window_does_not_open_or_close_inside_a_word() -> None:
    window = parent_window(PARENT, _child(5), HEADING)

    assert window.split()[0] in PARENT.split()
    assert window.split()[-1] in PARENT.split()


def test_child_missing_from_the_parent_falls_back_to_the_child_text() -> None:
    child = f"{HEADING}\n\nText that was edited after the parent was written."

    assert parent_window(PARENT, child, HEADING) == child


def _state(serving: list[dict], parents: dict[int, str]) -> SimpleNamespace:
    return SimpleNamespace(
        serving=serving,
        parent_text_by_id=parents,
        chunks_out=[],
        trace=MagicMock(),
        ranking_contract_mode="off",
    )


def test_served_chunk_carries_window_text_with_heading_title_and_url() -> None:
    serving = [
        {
            "chunk_id": "c1",
            "text": _child(5),
            "score": 0.9,
            "parent_chunk_id": 7,
            "heading_path": HEADING,
            "title": "Invoices",
            "source_url": "https://example.com/help/invoices",
        }
    ]
    state = _state(serving, {7: PARENT})

    _run_response_build(state)  # type: ignore[arg-type]

    (chunk,) = state.chunks_out
    assert PARAGRAPHS[5] in chunk.text
    assert len(chunk.text) <= PARENT_WINDOW_CHARS
    assert chunk.is_parent_text is True
    assert chunk.heading_path == HEADING
    assert chunk.title == "Invoices"
    assert chunk.source_url == "https://example.com/help/invoices"
