"""Tests for retrieval_api.util.payload.payload_list (SPEC-CRAWLER-005 AC-04.1)."""
from __future__ import annotations

import pytest

from retrieval_api.util.payload import payload_list


@pytest.mark.parametrize(
    ("payload", "key", "expected"),
    [
        # Real list → shallow copy (same content)
        ({"anchor_texts": ["a", "b"]}, "anchor_texts", ["a", "b"]),
        ({"links_to": ["https://x", "https://y"]}, "links_to", ["https://x", "https://y"]),
        ({"image_urls": []}, "image_urls", []),
        # Missing key → empty
        ({}, "anchor_texts", []),
        ({"other": 1}, "links_to", []),
        # None → empty
        ({"anchor_texts": None}, "anchor_texts", []),
        # Non-list scalar → empty (no silent coercion)
        ({"links_to": "oops"}, "links_to", []),
        ({"image_urls": 42}, "image_urls", []),
        ({"anchor_texts": {"not": "a list"}}, "anchor_texts", []),
    ],
)
def test_payload_list_shapes(payload: dict, key: str, expected: list) -> None:
    """payload_list handles every input shape (AC-04.1)."""
    assert payload_list(payload, key) == expected


def test_payload_list_returns_copy_not_reference() -> None:
    """Mutating the returned list must not mutate the underlying payload."""
    original = ["a", "b"]
    payload = {"anchor_texts": original}

    result = payload_list(payload, "anchor_texts")
    result.append("c")

    assert original == ["a", "b"]
    assert payload["anchor_texts"] == ["a", "b"]
