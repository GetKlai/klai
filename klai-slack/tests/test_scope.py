from __future__ import annotations

import pytest

from app.services.scope import effective_kb_ids

ALLOWED = [1, 2, 3]


@pytest.mark.parametrize(
    ("channel", "thread", "expected"),
    [
        (None, None, None),  # nothing stored: all allowed, omit knowledge_base_ids
        ([2], None, [2]),  # channel default applies
        ([2], [1], [1]),  # thread scope wins over the channel default
        (None, [3], [3]),  # thread scope without a channel default
        ([2, 9], None, [2]),  # revoked KB 9 drops out, the rest stays
        ([9], None, []),  # stored choice fully revoked: "kies opnieuw", never widen
        ([2], [9], []),  # an emptied thread scope does not fall back to the channel
    ],
)
def test_effective_scope(
    channel: list[int] | None, thread: list[int] | None, expected: list[int] | None
) -> None:
    assert effective_kb_ids(ALLOWED, channel, thread) == expected
