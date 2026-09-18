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
nothing after turn one is worth reading. Conversations that never complete are
excluded from that comparison on both sides, so a widget outage does not
silently move the ratio.

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
_CONVERSATIONS = text(
    """
    SELECT m.conversation_id
    FROM widget_messages m
    JOIN widget_conversations c ON c.id = m.conversation_id
    WHERE c.widget_id = :widget_id
      AND NOT c.is_preview AND NOT c.is_test
      AND m.role = 'user'
      AND m.created_at > now() - make_interval(days => 60)
    GROUP BY m.conversation_id
    HAVING count(*) >= 2
    ORDER BY max(m.created_at) DESC
    LIMIT :limit
    """
)

# Every visitor turn of a chosen conversation, in the order they were sent.
# Filtering on length or age before the aggregate silently promoted a later
# follow-up to "the real first question", which is the one thing turn one may
# never be. ``sequence`` rather than ``created_at``: two messages can share a
# timestamp, and then the opening turn is whichever the planner happens to emit.
_TURNS = text(
    """
    SELECT conversation_id::text AS cid, content
    FROM widget_messages
    WHERE conversation_id = ANY(:ids) AND role = 'user'
    ORDER BY conversation_id, sequence
    """
)

_GOAL_SYSTEM = (
    "You read what one visitor typed to a help assistant. Write, in their language and in one "
    "sentence, what they were trying to get done. Describe only their situation and what they "
    "wanted; never mention the assistant, its answers, or anything they only asked because of an "
    "answer they were given."
)

_VISITOR_SYSTEM = (
    "You are a visitor on a company's help page, talking to its chat assistant. You have one goal, "
    "given below, taken from a real conversation. Write ONLY your next message to the assistant, in the "
    "language of your goal, the way a person types on a help page: short, no pleasantries, no explaining "
    "yourself. Do not invent facts about your own situation beyond the goal. Reply with exactly DONE only "
    "when your goal has been answered. If the assistant says it cannot help or offers a person instead, "
    "try once more first: rephrase your question, or add one detail that is already in your goal. If it "
    "still cannot help after that, reply with exactly: DONE."
)

_SCORE_SYSTEM = (
    "You read a conversation between a visitor and a company's help assistant, and the goal the visitor "
    'had. Answer with JSON only: {"reached": true|false, "handed_off": true|false, "turns_wasted": <int>, '
    '"why": "<8 words>"}. reached — did the visitor end up with information they can act on for the goal. '
    "A hand-off to a person is never reached, even when the assistant was honest about not finding it. "
    "handed_off — did the assistant say it could not find the answer and point the visitor to a person. "
    "turns_wasted — how many assistant turns said nothing the visitor could act on."
)

# Model for the goal summary, the simulated visitor and the scorer. Not
# klai-primary/klai-fast: those share the live widget's 100 RPM quota, and an
# unpaced run against that quota caused the 502 incident of 2026-09-18. Not
# klai-medium either: that is answer_grounding_model, the model under test —
# scoring it with itself would judge the grounding check by its own opinion.
# klai-large is Mistral Large at 13 RPM per key (two keys), which is why the
# existing pacing below (6s between conversations, 1.5s between turns, at
# most five model calls per conversation) is what keeps this harness under
# that limit.
_SIMULATION_MODEL = os.getenv("KLAI_SIMULATION_MODEL") or "klai-large"


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


async def _goals(widget_id: str, limit: int, client: httpx.AsyncClient) -> list[dict]:
    """Real conversations, each reduced to what that visitor was trying to get done.

    The recorded follow-up turns cannot be the goal: they are answers to the old
    system's answers, so feeding them back would steer the simulation down the
    path it exists to leave. One sentence of intent is derived from them instead.
    """
    async with cross_org_session() as session:
        ids = [
            row[0] for row in (await session.execute(_CONVERSATIONS, {"widget_id": widget_id, "limit": limit})).all()
        ]
        if not ids:
            return []
        rows = (await session.execute(_TURNS, {"ids": ids})).mappings().all()

    per_conversation: dict[str, list[str]] = {}
    for row in rows:
        per_conversation.setdefault(row["cid"], []).append(row["content"])

    goals = []
    for cid, beurten in per_conversation.items():
        if len(beurten) < 2:
            continue
        doel = await _model(client, _GOAL_SYSTEM, "\n".join(beurten), max_tokens=80)
        goals.append({"cid": cid, "eerste": beurten[0], "doel": doel or beurten[0]})
    return goals


async def _model(client: httpx.AsyncClient, system: str, user: str, *, max_tokens: int = 200) -> str:
    async def _call():
        return await client.post(
            f"{settings.litellm_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
            json={
                "model": _SIMULATION_MODEL,
                "temperature": 0.3,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )

    response = await _with_backoff("het model", lambda: _post_and_raise(_call))
    return response.json()["choices"][0]["message"]["content"].strip()


# One simulated conversation costs up to four widget turns and five model calls,
# and every widget turn spends three more calls of its own on the same klai-fast
# budget a real visitor uses. Run unpaced on 2026-09-18 this exhausted that
# budget: the shared alias started answering 429, the widget returned 502, and a
# real visitor asking a question in that window would have got an error. The
# pause below is the same shape as the ingest service's client-side limit
# (knowledge_ingest.config.litellm_klai_fast_rps), and it is not optional.
_PAUSE_BETWEEN_CONVERSATIONS = float(os.getenv("KLAI_SIMULATION_PAUSE") or "6.0")
_PAUSE_BETWEEN_TURNS = 1.5
_BACKOFF_SECONDS = (5.0, 15.0, 45.0)


async def _with_backoff(what: str, call):
    """Retry a throttled or briefly failing call instead of hammering through it."""
    for wait in (*_BACKOFF_SECONDS, None):
        try:
            return await call()
        except httpx.HTTPStatusError as exc:
            throttled = exc.response is not None and exc.response.status_code in (429, 502, 503)
            if not throttled or wait is None:
                raise
            print(f"    {what} kreeg {exc.response.status_code}, {wait:.0f}s wachten", flush=True)
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")


def _widget_api_base() -> str:
    """Where the widget's public chat route lives; the script talks to the real one."""
    return os.getenv("KLAI_WIDGET_API_BASE") or f"https://api.{settings.domain}"


async def _post_and_raise(call):
    response = await call()
    response.raise_for_status()
    return response


async def _widget_reply(client: httpx.AsyncClient, token: str, messages: list[dict]) -> tuple[str, int]:
    async def _call():
        return await client.post(
            f"{_widget_api_base()}/partner/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"model": "klai-primary", "stream": False, "messages": messages},
        )

    response = await _with_backoff("de widget", lambda: _post_and_raise(_call))
    message = response.json()["choices"][0]["message"]
    return message.get("content") or "", len(message.get("sources") or [])


async def _one_conversation(client: httpx.AsyncClient, token: str, goal: dict, max_turns: int) -> dict:
    """The visitor's own first words, then a simulated visitor for the rest."""
    doel = goal["doel"]
    messages: list[dict] = [{"role": "user", "content": goal["eerste"]}]
    transcript: list[str] = [f"Visitor: {goal['eerste']}"]
    eerste_bronnen = 0

    for turn in range(max_turns):
        antwoord, bronnen = await _widget_reply(client, token, messages)
        if turn == 0:
            eerste_bronnen = bronnen
        messages.append({"role": "assistant", "content": antwoord})
        transcript.append(f"Assistant: {antwoord}")
        if turn == max_turns - 1:
            break
        await asyncio.sleep(_PAUSE_BETWEEN_TURNS)
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
    score = _parse_score(raw)
    return {
        "cid": goal["cid"],
        "eerste_vraag": goal["eerste"],
        "doel": doel,
        "beurten": sum(1 for line in transcript if line.startswith("Assistant:")),
        "eerste_bronnen": eerste_bronnen,
        "bereikt": score.get("reached"),
        "doorverwezen": score.get("handed_off"),
        "verspild": score.get("turns_wasted"),
        "waarom": score.get("why"),
        "transcript": transcript,
    }


def _parse_score(raw: str) -> dict:
    """A score is only a score when it has the right shape.

    Parseable JSON with the wrong types used to pass straight into the totals,
    where ``reached=None`` then counted as a failure. Anything unusable is
    reported on its own line instead of quietly lowering the success rate.
    """
    try:
        parsed = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    except Exception:
        return {"reached": None, "handed_off": None, "turns_wasted": None, "why": "score unparseable"}
    if not isinstance(parsed.get("reached"), bool) or not isinstance(parsed.get("handed_off"), bool):
        return {"reached": None, "handed_off": None, "turns_wasted": None, "why": "score has no verdict"}
    wasted = parsed.get("turns_wasted")
    return {
        "reached": parsed["reached"],
        "handed_off": parsed["handed_off"],
        "turns_wasted": wasted if isinstance(wasted, int) else None,
        "why": str(parsed.get("why") or "")[:60],
    }


async def main(widget_id: str, aantal: int, beurten: int) -> None:
    if _SIMULATION_MODEL == settings.answer_grounding_model:
        raise SystemExit("De simulatie mag niet op hetzelfde model draaien als de grounding check die ze meet.")

    token, _ = await _session_token(widget_id)

    resultaten: list[dict] = []
    mislukt: list[str] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        goals = await _goals(widget_id, aantal, client)
        if not goals:
            print("Geen echte gesprekken met meerdere beurten gevonden voor deze widget.")
            return
        for index, goal in enumerate(goals, 1):
            try:
                resultaat = await _one_conversation(client, token, goal, beurten)
            except Exception as exc:  # one broken conversation may not stop the run
                mislukt.append(f"{goal['cid']}: {exc!r}")
                print(f"{index}/{len(goals)} mislukt: {exc!r}", flush=True)
                continue
            resultaten.append(resultaat)
            print(
                f"{index}/{len(goals)} beurten={resultaat['beurten']} bereikt={resultaat['bereikt']} "
                f"verspild={resultaat['verspild']} | {resultaat['eerste_vraag'][:50]}",
                flush=True,
            )
            await asyncio.sleep(_PAUSE_BETWEEN_CONVERSATIONS)

    if not resultaten:
        print("Geen enkel gesprek kwam rond.")
        return

    # Conversations without a usable verdict leave the denominator: counting them
    # as failures would move the number this harness exists to compare.
    beoordeeld = [r for r in resultaten if r["bereikt"] is not None]
    onbeoordeeld = len(resultaten) - len(beoordeeld)
    n = len(beoordeeld) or 1
    bereikt = sum(1 for r in beoordeeld if r["bereikt"])
    doorverwezen = sum(1 for r in beoordeeld if r["doorverwezen"])
    met_bron = sum(1 for r in resultaten if r["eerste_bronnen"] > 0)
    verspild = [r["verspild"] for r in beoordeeld if isinstance(r["verspild"], int)]

    print(f"\n=== {len(resultaten)} GESIMULEERDE GESPREKKEN ===")
    print(f"doel bereikt: {bereikt} van {len(beoordeeld)} beoordeeld ({round(100 * bereikt / n)}%)")
    print(f"eerlijk doorverwezen zonder antwoord: {doorverwezen} van {len(beoordeeld)} beoordeeld")
    if onbeoordeeld:
        print(f"niet te beoordelen (buiten de noemer): {onbeoordeeld}")
    if mislukt:
        print(f"gesprekken die niet rondkwamen: {len(mislukt)}")
        for line in mislukt[:3]:
            print(f"  {line}")
    ijkpunt = (
        f"eerste antwoord met bron: {met_bron} van {len(resultaten)} "
        f"({round(100 * met_bron / len(resultaten))}%)  <- ijkpunt tegen de replay; mislukte "
        "gesprekken staan aan beide kanten buiten de noemer"
    )
    if mislukt:
        ijkpunt += f" ({len(mislukt)} mislukt)"
    print(ijkpunt)
    print(f"gemiddeld aantal assistentbeurten: {sum(r['beurten'] for r in resultaten) / len(resultaten):.1f}")
    if verspild:
        print(f"gemiddeld verspilde beurten: {sum(verspild) / len(verspild):.1f}")

    doel_bestand = Path(os.getenv("KLAI_SIMULATION_OUT") or (tempfile.gettempdir() + "/simulated_conversations.json"))
    doel_bestand.write_text(json.dumps(resultaten, ensure_ascii=False))
    print(f"transcripten: {doel_bestand}")


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1],
            int(sys.argv[2]) if len(sys.argv) > 2 else 12,
            int(sys.argv[3]) if len(sys.argv) > 3 else 4,
        )
    )
