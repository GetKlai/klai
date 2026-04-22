"""Qdrant payload read helpers.

SPEC-CRAWLER-005 REQ-04: Qdrant strips empty-list payload keys on upsert,
so absent and ``[]`` are equivalent at the storage layer. Every retrieval
consumer of list-shaped payload keys (``anchor_texts``, ``links_to``,
``image_urls``) MUST read through :func:`payload_list` to avoid silent
drift between the two shapes.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def payload_list(payload: Mapping[str, Any], key: str) -> list:
    """Return a list for ``payload[key]`` regardless of input shape.

    Treats key-absent, ``None``, and non-list values as ``[]``. A real list
    is passed through as a shallow copy so callers cannot mutate the
    underlying payload.

    SPEC: SPEC-CRAWLER-005 REQ-04.1.
    """
    value = payload.get(key)
    return list(value) if isinstance(value, list) else []
