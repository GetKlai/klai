"""Shared pieces of the model calls that read a turn before anything is written.

``structured_judge_call`` is the one strict-json_schema call the case
(case_file.py) and the selection step (passage_selection.py) are made with;
``conversation_excerpt`` is the window of the conversation they read. The
question judge that lived here, one call for scope, tone, wants-a-person and
clarity, was replaced by the case on 2026-10-12 (plan §8, step M): on 215
stored turns the two agreed on the conversational class in every turn, on
tone in 197, and on the off-topic subject in 108 of 109.

Scope, the measured reason it is an enum and not a boolean: the first version
of the scope classifier asked one boolean and told the model that "yes, this
needs grounding" was always the safe choice. On klai-fast it then answered
exactly that for EVERY message, including "dankjewel". Probed against the
running service on 2026-09-15, naming the three classes and making the model
pick one scored 11/11 on the same set, where the boolean scored 0 on the six
conversational cases.

Caller-supplied ``system`` messages never reach a judge: only ``user`` and
``assistant`` turns are rendered, the same filter the answer model gets.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog
from pydantic import BaseModel

from app.core.config import Settings
from app.services.litellm_delegation import with_delegated_org, with_feature_tag

logger = structlog.get_logger()

# Six turns is three exchanges, the same window retrieval-api uses for
# coreference. Clipped per turn so one pasted log cannot fill the prefill that
# the 2 s budget is spent on; the latest visitor message is rarely that long.
_EXCERPT_TURNS = 6
_EXCERPT_TURN_CHARS = 800


async def structured_judge_call[J: BaseModel](
    *,
    name: str,
    system_prompt: str,
    user_content: str,
    schema: type[J],
    timeout_seconds: float,
    settings: Settings,
    model: str | None = None,
    delegated_org_id: str | None = None,
) -> J | None:
    """One strict-json_schema call, on klai-fast unless ``model`` says otherwise;
    ``None`` and ``<name>_failed`` on any failure.

    ``delegated_org_id`` is set on an internal-chat turn, for the same reason as
    on its answer call: the master key belongs to no tenant, so LiteLLM masks
    personal data only when the employee's org travels with the call.

    The LiteLLM spend tag is derived from ``name`` (``portal:<name-with-dashes>``)
    -- every caller already passes a distinguishing ``name`` for the json_schema
    and its failure log event, so this reuses it rather than adding a second
    parameter every caller must remember to set.
    """
    failure_event = f"{name}_failed"
    body: dict = {
        "model": model or settings.extraction_model,
        # The same turn must get the same verdict: without a
        # fixed temperature 5 of 9 replayed questions flipped.
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema.model_json_schema()},
        },
        # LiteLLM's router falls back from a failing klai-fast to klai-large, a
        # fallback meant for interactive chat. A judge that fails fast already
        # has a safe fail direction, so it opts out per request and stays on Small.
        # ``fallbacks: []`` and not ``disable_fallbacks``: the latter also skips
        # the same-model order-based failover (see knowledge-ingest
        # llm_throttle.add_no_fallback for the verified reasoning).
        "fallbacks": [],
    }
    with_delegated_org(body, delegated_org_id)
    with_feature_tag(body, f"portal:{name.replace('_', '-')}")
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{settings.litellm_base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                    json=body,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return schema.model_validate_json(content, strict=True)
    except Exception:
        logger.warning(failure_event, exc_info=True)
    return None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


def conversation_excerpt(messages: list[dict], *, latest_chars: int = _EXCERPT_TURN_CHARS) -> str:
    """The last few user/assistant turns, clipped, with the latest visitor message marked.

    ``latest_chars`` clips the latest visitor message alone; every other turn
    keeps the per-turn limit.
    """
    turns: list[tuple[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            turns.append((role, content.strip()))
    turns = turns[-_EXCERPT_TURNS:]
    latest_visitor = max((i for i, (role, _) in enumerate(turns) if role == "user"), default=-1)
    lines = []
    for index, (role, content) in enumerate(turns):
        label = "Assistant" if role == "assistant" else "Visitor"
        limit = _EXCERPT_TURN_CHARS
        if index == latest_visitor:
            label = "Visitor (LATEST message)"
            limit = latest_chars
        lines.append(f"{label}: {_clip(content, limit)}")
    return "\n\n".join(lines)


def is_conversational(scope: str | None) -> bool:
    """One place decides how ``None`` reads, so no call site can get it wrong."""
    return scope == "conversation"


def scope_label(scope: str | None) -> str:
    """The scope as one queryable word, for all three outcomes and a failed judge.

    ``judge_failed`` is kept apart from ``organisation`` even though both behave
    identically: a rising failure rate is a signal about the judge rather than
    about visitors, and a share you cannot see is a boundary that drifts.
    """
    return scope or "judge_failed"


# Appended to the system prompt for a conversational turn. The profile the model
# receives otherwise tells it to say the help articles do not cover this and to
# offer support -- correct for a question about the organisation, wrong for
# "do you speak English", and the composer would pass such a self-written
# refusal straight through because it never looks at the words. Same shape as
# escalation_intent.ESCALATION_TURN_ADDENDUM: one turn, one instruction.
CONVERSATIONAL_TURN_ADDENDUM = (
    "\n\n[This turn] The visitor's message is about this conversation itself, "
    "not about the organisation — which languages you speak, a greeting, a "
    "thank-you, or a remark about the chat. Answer it directly and briefly in "
    "your own words. Do NOT say the help articles do not cover it, do NOT offer "
    "to look more broadly, and do NOT point to support: none of those is an "
    "answer to what was asked. Keep the rule that you never write URLs or "
    "citations. If the message ALSO asks something about the organisation, "
    "answer that part only from the help articles as usual."
)
