"""``structured_judge_call``'s LiteLLM spend tag.

SPEC: every LiteLLM call is attributable to the feature that made it
(LiteLLM_SpendLogs.request_tags, from metadata.tags). The tag is derived from
the caller's own ``name`` (turn_judge -> "portal:turn-judge",
query_paraphrase -> "portal:query-paraphrase") rather than a second parameter
every caller must remember to pass.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.services.turn_judge import structured_judge_call


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: bool


async def _call(name: str) -> dict:
    posted: dict = {}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            posted["json"] = json
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}
            return resp

    with patch("httpx.AsyncClient", _Client):
        await structured_judge_call(
            name=name,
            system_prompt="sys",
            user_content="user",
            schema=_Reply,
            timeout_seconds=1.0,
            settings=settings,
        )
    return posted["json"]


@pytest.mark.asyncio
async def test_turn_judge_name_maps_to_its_own_tag():
    body = await _call("turn_judge")
    assert body["metadata"]["tags"] == ["portal:turn-judge"]


@pytest.mark.asyncio
async def test_query_paraphrase_name_maps_to_its_own_tag():
    body = await _call("query_paraphrase")
    assert body["metadata"]["tags"] == ["portal:query-paraphrase"]
