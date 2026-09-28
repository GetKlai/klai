"""Which LiteLLM answers mean "no capacity left for this alias".

klai-judge shares the Vibe allowance with klai-ingest and has no fallback, so
a full allowance (Mistral 402, passed through by the proxy) or a spent daily
budget / every account FULL (LiteLLM's "No deployments available", which
litellm==1.96.2's ProxyException turns into 429 at proxy/_types.py:3465) is
an expected state, not a per-conversation failure. An ordinary rate-limit 429
is not capacity: the proxy already retried it.
"""

from __future__ import annotations

import httpx
import pytest

from app.services.llm_capacity import is_llm_capacity_error


def _status_error(status: int, message: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://litellm.example.com/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return httpx.HTTPStatusError(message, request=request, response=response)


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (402, "Workspace monthly spending limit reached", True),
        (429, "No deployments available - crossed budget: klai-judge", True),
        (429, "Model rate limit exceeded", False),
        (500, "No deployments available", False),
    ],
)
def test_capacity_is_a_402_or_a_no_deployments_429(status: int, message: str, expected: bool) -> None:
    assert is_llm_capacity_error(_status_error(status, message)) is expected


def test_a_capacity_answer_raised_from_another_error_still_counts() -> None:
    try:
        try:
            raise _status_error(402, "Workspace monthly spending limit reached")
        except httpx.HTTPStatusError as exc:
            raise RuntimeError("judge call failed") from exc
    except RuntimeError as wrapped:
        assert is_llm_capacity_error(wrapped) is True
