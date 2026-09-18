"""Run whole conversations against a widget with a simulated visitor.

Replaying a first question measures the first answer, and that is all it can
measure: the second question a visitor asks depends on the answer they just got,
so a recorded follow-up belongs to the conversation the old system produced, not
to the one being tested. Every end-to-end comparison so far therefore covers
turn one only (SPEC-RAG-ANSWER-JUDGES-001 §2.4, §2.15).

This harness closes that gap the way the literature does: a model plays the
visitor, carrying a goal taken from a real conversation, and talks to the live
widget for several turns. The first message is the real visitor's own words, not
generated, so the first turn of every simulated conversation can be compared
against the replay measurements — if those disagree, the simulator is wrong and
nothing after turn one is worth reading.

What it is not: real traffic. A simulated visitor is more patient and more
articulate than a person on a help page. Use it to compare two versions against
each other, never to claim an absolute success rate.

Run it against preview sessions so nothing lands in the tenant's conversations:

    docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 \
        python scripts/simulate_conversations.py <widget_uuid> [conversations] [turns]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    # Same bootstrap as the other operator scripts: running "python scripts/x.py"
    # puts scripts/ on the path, not the backend root, so ``app`` would not import.
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import cross_org_session  # noqa: E402
from app.models.portal import PortalOrg  # noqa: E402
from app.models.widgets import Widget, WidgetKbAccess  # noqa: E402
from app.services.widget_auth import generate_session_token  # noqa: E402

# Real conversations with at least two visitor turns: one turn is a replay, and
# the divergence this harness exists for only starts at the second.
_GOALS = text(
    """
    SELECT m.conversation_id::text AS cid,
           array_agg(m.content ORDER BY m.created_at) AS beurten
    FROM widget_messages m
    JOIN widget_conversations c ON c.id = m.conversation_id
    WHERE c.org_id = :org_id
      AND NOT c.is_preview AND NOT c.is_test
      AND m.role = 'user'
      AND m.created_at > now() - make_interval(days => 60)
      AND length(m.content) BETWEEN 8 AND 400
    GROUP BY m.conversation_id
    HAVING count(*) >= 2
    ORDER BY max(m.created_at) DESC
    LIMIT :limit
    """
)

_VISITOR_SYSTEM = (
    "You are a visitor on a company's help page, talking to its chat assistant. You have one goal, "
    "given below, taken from a real conversation. Write ONLY your next message to the assistant, in the "
    "language of your goal, the way a person types on a help page: short, no pleasantries, no explaining "
    "yourself. Do not invent facts about your own situation beyond the goal. If the assistant has answered "
    "your goal, or has made clear it cannot and offered a person, reply with exactly: DONE."
)

_SCORE_SYSTEM = (
    "You read a conversation between a visitor and a company's help assistant, and the goal the visitor "
    'had. Answer with JSON only: {"reached": true|false, "turns_wasted": <int>, "why": "<8 words>"}. '
    "reached — did the visitor end up with what the goal asked for, or with an honest statement that it is "
    "not in the help articles plus a way to reach a person. turns_wasted — how many assistant turns said "
    "nothing the visitor could act on."
)


async def _session_token(widget_id: str) -> tuple[str, str]:
    from sqlalchemy import select

    async with cross_org_session() as session:
        widget = (await session.execute(select(Widget).where(Widget.id == widget_id))).scalar_one()
        org = (await session.execute(select(PortalOrg).where(PortalOrg.id == widget.org_id))).scalar_one()
        kb_ids = [
            row.kb_id
            for row in (await session.execute(select(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget.id)))
            .scalars()
            .all()
        ]
    token = generate_session_token(
        wgt_id=widget.widget_id,
        org_id=widget.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=org.slug,
        is_preview=True,
    )
    return token, str(widget.org_id)


async def _goals(org_id: int, limit: int) -> list[dict]:
    async with cross_org_session() as session:
        rows = (await session.execute(_GOALS, {"org_id": org_id, "limit": limit})).mappings().all()
    return [{"cid": row["cid"], "beurten": list(row["beurten"])} for row in rows]


async def _model(client: httpx.AsyncClient, system: str, user: str, *, max_tokens: int = 200) -> str:
    response = await client.post(
        f"{settings.litellm_base_url}/chat/completions",
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        json={
            "model": settings.answer_grounding_model,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()


def _widget_api_base() -> str:
    """Where the widget's public chat route lives; the script talks to the real one."""
    return os.getenv("KLAI_WIDGET_API_BASE") or f"https://api.{settings.domain}"


async def _widget_reply(client: httpx.AsyncClient, token: str, messages: list[dict]) -> tuple[str, int]:
    response = await client.post(
        f"{_widget_api_base()}/partner/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"model": "klai-primary", "stream": False, "messages": messages},
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    return message.get("content") or "", len(message.get("sources") or [])


async def _one_conversation(client: httpx.AsyncClient, token: str, goal: dict, max_turns: int) -> dict:
    """The visitor's own first words, then a simulated visitor for the rest."""
    doel = " | ".join(goal["beurten"])
    messages: list[dict] = [{"role": "user", "content": goal["beurten"][0]}]
    transcript: list[str] = [f"Visitor: {goal['beurten'][0]}"]
    eerste_bronnen = 0

    for turn in range(max_turns):
        antwoord, bronnen = await _widget_reply(client, token, messages)
        if turn == 0:
            eerste_bronnen = bronnen
        messages.append({"role": "assistant", "content": antwoord})
        transcript.append(f"Assistant: {antwoord}")
        if turn == max_turns - 1:
            break
        volgende = await _model(
            client,
            _VISITOR_SYSTEM,
            f"Your goal:\n{doel}\n\nThe conversation so far:\n" + "\n\n".join(transcript),
            max_tokens=80,
        )
        if volgende.upper().startswith("DONE") or not volgende:
            break
        messages.append({"role": "user", "content": volgende})
        transcript.append(f"Visitor: {volgende}")

    raw = await _model(
        client,
        _SCORE_SYSTEM,
        f"Goal:\n{doel}\n\nConversation:\n" + "\n\n".join(transcript),
        max_tokens=80,
    )
    try:
        score = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    except Exception:
        score = {"reached": None, "turns_wasted": None, "why": "score unparseable"}
    return {
        "cid": goal["cid"],
        "eerste_vraag": goal["beurten"][0],
        "beurten": sum(1 for line in transcript if line.startswith("Assistant:")),
        "eerste_bronnen": eerste_bronnen,
        "bereikt": score.get("reached"),
        "verspild": score.get("turns_wasted"),
        "waarom": score.get("why"),
        "transcript": transcript,
    }


async def main(widget_id: str, aantal: int, beurten: int) -> None:
    token, org_id = await _session_token(widget_id)
    goals = await _goals(int(org_id), aantal)
    if not goals:
        print("Geen echte gesprekken met meerdere beurten gevonden.")
        return

    resultaten = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        for index, goal in enumerate(goals, 1):
            try:
                resultaat = await _one_conversation(client, token, goal, beurten)
            except Exception as exc:  # one broken conversation may not stop the run
                print(f"{index}/{len(goals)} mislukt: {exc!r}", flush=True)
                continue
            resultaten.append(resultaat)
            print(
                f"{index}/{len(goals)} beurten={resultaat['beurten']} bereikt={resultaat['bereikt']} "
                f"verspild={resultaat['verspild']} | {resultaat['eerste_vraag'][:50]}",
                flush=True,
            )

    n = len(resultaten)
    if not n:
        return
    bereikt = sum(1 for r in resultaten if r["bereikt"] is True)
    met_bron = sum(1 for r in resultaten if r["eerste_bronnen"] > 0)
    verspild = [r["verspild"] for r in resultaten if isinstance(r["verspild"], int)]
    print(f"\n=== {n} GESIMULEERDE GESPREKKEN ===")
    print(f"doel bereikt: {bereikt} ({round(100 * bereikt / n)}%)")
    print(f"eerste antwoord met bron: {met_bron} ({round(100 * met_bron / n)}%)  <- ijkpunt tegen de replay")
    print(f"gemiddeld aantal assistentbeurten: {sum(r['beurten'] for r in resultaten) / n:.1f}")
    if verspild:
        print(f"gemiddeld verspilde beurten: {sum(verspild) / len(verspild):.1f}")
    doel = Path(os.getenv("KLAI_SIMULATION_OUT") or (tempfile.gettempdir() + "/simulated_conversations.json"))
    doel.write_text(json.dumps(resultaten, ensure_ascii=False))
    print(f"transcripten: {doel}")


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1],
            int(sys.argv[2]) if len(sys.argv) > 2 else 12,
            int(sys.argv[3]) if len(sys.argv) > 3 else 4,
        )
    )
