"""The agent prompt names only tools, and assertion modes, that the server accepts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROMPT = Path(__file__).resolve().parents[1] / "agent-system-prompt.md"


def _code_spans(text: str) -> set[str]:
    return set(re.findall(r"`([^`\n]+)`", text))


@pytest.mark.asyncio
async def test_prompt_names_only_registered_tools() -> None:
    import main

    registered = {t.name for t in await main.mcp.list_tools()}
    named = {s for s in _code_spans(PROMPT.read_text()) if re.fullmatch(r"[a-z]+(_[a-z]+)+", s)}
    assert "save_personal_knowledge" in named
    assert named - registered == set()


def test_prompt_assertion_modes_are_accepted_by_the_save_tool() -> None:
    import main

    block = PROMPT.read_text().split("**assertion_mode**", 1)[1].split("**tags**", 1)[0]
    modes = set(re.findall(r"^- `([a-z]+)`", block, flags=re.M))
    assert modes
    assert modes <= main.VALID_ASSERTION_MODES
