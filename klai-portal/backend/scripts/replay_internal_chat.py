"""Replay real internal-chat conversations through the old and the new chat pipeline, judged blind.

docs/architecture/chat-quality-history-and-plan.md §7.4: before the internal
chat moves from the LiteLLM knowledge hook (OLD) to the one chat pipeline in
portal-api (NEW), both answer the same real employees, and a judge that does
not know which is which decides per turn which answer served the employee
better.

Method, and the lesson behind each part (§3 and §4.3 of that document):
- A simulated employee carries a goal derived from one real conversation of
  that org and opens with the employee's own first message verbatim; both
  paths get the same employee (same model, temperature 0, same goal, same
  first message), sent as that real user, so mode, scope and knowledge bases
  are the employee's own.
- Every turn is judged, not only the first: a clarifying question is judged by
  what the next turn made of it, because judging the first turn alone rewards
  a complete answer over a good question.
- LLM judges prefer the answer shown first, so every pair is judged in both
  orders and a disagreement counts as a tie.
- Around 47 non-tie pairs are needed to see a 70/30 preference (sign test,
  alpha 0.05, power 80%); the summary prints the number of non-tie pairs.

OLD is exactly the call a tenant's LibreChat makes: POST to LiteLLM's
/v1/chat/completions with the tenant's own LiteLLM key (the LITELLM_API_KEY that
provisioning wrote into the tenant's LibreChat .env, whose key metadata carries
the org) and the OpenAI ``user`` field set to the employee's LibreChat user id.
NEW runs ``partner.chat_completions`` in-process with the internal profile
resolved for that same user, so no API key with ``internal_chat`` is needed.
Neither path gets LibreChat's promptPrefix or MCP tools, so both answer the
same messages.

Side effects, so a run can be read against production data: NEW turns are not
written to ``internal_chat_turns`` (the script captures their signals instead)
and file no knowledge gaps. OLD turns go through the live hook, which does log
its retrieval and files gap events for the replayed questions, and whose
LiteLLM spend lands on the tenant's team.

PRIVATE EMPLOYEE DATA: this reads real conversations. Every model call carries
the org (the master-key calls through ``with_delegated_org``, the OLD call
through its tenant key), so LiteLLM applies the tenant's PII policy. Transcripts
and the pipeline's own logs go to a directory outside the repository (default
/tmp/replay-<org>-<timestamp>/, or KLAI_REPLAY_OUT); stdout shows counts only.

A run takes tens of minutes, and ``docker exec`` runs inside the compose
service: a deploy of portal-api recreates that container mid-run and kills the
process. Run it in a throwaway clone instead, via deploy/scripts/portal-api-oneoff.sh,
and point KLAI_REPLAY_OUT at the one-off's /out mount so an interrupted run can
be resumed by pointing at the same folder again:

    deploy/scripts/portal-api-oneoff.sh -- \\
        env KLAI_REPLAY_OUT=/out python scripts/replay_internal_chat.py <org_slug> [conversations] [turns]
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    # Same bootstrap as the other operator scripts: running "python scripts/x.py"
    # puts scripts/ on the path, not the backend root, so ``app`` would not import.
    sys.path.insert(0, str(BACKEND_ROOT))

import httpx  # noqa: E402
import simulate_conversations as sim  # noqa: E402
from sqlalchemy import select  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import StreamingResponse  # noqa: E402

from app.api import partner  # noqa: E402
from app.api.partner_dependencies import PartnerAuthContext  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import cross_org_session, tenant_scoped_session  # noqa: E402
from app.core.provisioning_names import provisioning_names_for_slug  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402
from app.models.portal import PortalOrg  # noqa: E402
from app.services.chat_profile import ChatProfile, resolve_internal_profile  # noqa: E402
from app.services.internal_chat_identity import LibreChatIdentityError  # noqa: E402
from app.services.librechat_quality_judge import (  # noqa: E402
    _mongo_client,
    _sync_fetch_messages,
    _turns_from_messages,
)
from app.services.provisioning.infrastructure import _read_dotenv_file  # noqa: E402

REPO_ROOT = BACKEND_ROOT.parents[1]

# Candidate conversations: the newest ones updated in this window. A LibreChat
# thread can run for weeks, so the window bounds the read, not the age of the
# first message.
_SAMPLE_DAYS = 90
_CANDIDATES = 400
# How long the NEW turn's own record may take after its stream ended: an Open
# turn's grounding check finishes after the reply went out.
_SIGNAL_WAIT_SECONDS = 120.0

_GOAL_SYSTEM = (
    "You read what one employee typed to their organisation's internal knowledge assistant. Write, in "
    "their language and in one sentence, what they were trying to get done. Describe only their situation "
    "and what they wanted; never mention the assistant, its answers, or anything they only asked because "
    "of an answer they were given."
)

_EMPLOYEE_SYSTEM = (
    "You are an employee asking your organisation's internal knowledge assistant for help with your work. "
    "You have one goal, given below, taken from a real conversation. Write ONLY your next message to the "
    "assistant, in the language of your goal, the way a colleague types in a work chat: short and to the "
    "point. When the assistant asks you a question, answer it using only what is in your goal. Do not "
    "invent facts about your situation beyond the goal. Reply with exactly DONE when your goal has been "
    "answered, or when the assistant has said twice that it cannot help."
)

_JUDGE_SYSTEM = (
    "You compare two answers that an organisation's internal knowledge assistant gave an employee at the "
    "same point of two separate conversations about the same goal. Each side shows the conversation so "
    "far, the ANSWER being judged, and sometimes WHAT FOLLOWED it. Decide which ANSWER served the employee "
    "better: grounded in the organisation's own knowledge rather than generic, correct, and complete "
    "enough to act on. An honest 'this is not in the knowledge base' beats an invented answer. When an "
    "ANSWER asks the employee a question, judge it by whether WHAT FOLLOWED then served the employee "
    "better than the other side, never by the question alone. The order in which the sides are shown "
    "means nothing. Answer with JSON only: "
    '{"better": "A"|"B"|"tie", "A": {"refused": true|false, "asked": true|false}, '
    '"B": {"refused": true|false, "asked": true|false}}. refused: the ANSWER said it could not help or '
    "found nothing. asked: the ANSWER asked the employee a question to clarify what they need."
)


@dataclass(frozen=True)
class Sample:
    cid: str
    librechat_user_id: str
    profile: ChatProfile
    asks: list[str]


Ask = Callable[[list[dict]], Awaitable[dict]]


def _out_dir(org_slug: str) -> Path:
    default = Path(tempfile.gettempdir()) / f"replay-{org_slug}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out = Path(os.getenv("KLAI_REPLAY_OUT") or default).resolve()
    if out == REPO_ROOT or REPO_ROOT in out.parents:
        raise SystemExit("KLAI_REPLAY_OUT must be outside the repository: the transcripts are real conversations.")
    # exist_ok: KLAI_REPLAY_OUT may point at a folder from an interrupted run,
    # which main() resumes by reading its turns.jsonl.
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    return out


def _done_cids(out: Path) -> set[str]:
    """Conversation ids already recorded in turns.jsonl, so a resumed run does not replay them again."""
    path = out / "turns.jsonl"
    if not path.exists():
        return set()
    with path.open() as handle:
        return {json.loads(line)["cid"] for line in handle if line.strip()}


def _route_logs_to(path: Path) -> None:
    """The pipeline logs queries and statements; in this process they go to a file, never to stdout."""
    setup_logging("replay-internal-chat")
    root = logging.getLogger()
    formatter = root.handlers[0].formatter
    root.handlers.clear()
    handler = logging.FileHandler(path)
    handler.setFormatter(formatter)
    root.addHandler(handler)


async def _load_org(org_slug: str) -> PortalOrg:
    # The only cross-org read: the org id has to be known before a tenant scope can be set.
    async with cross_org_session() as session:
        org = (
            await session.execute(select(PortalOrg).where(PortalOrg.slug == org_slug, PortalOrg.deleted_at.is_(None)))
        ).scalar_one_or_none()
    if org is None:
        raise SystemExit(f"No active org with slug {org_slug}.")
    return org


def _tenant_litellm_key(slug: str) -> str:
    """The key the tenant's LibreChat calls LiteLLM with, from the .env provisioning wrote for it."""
    return _read_dotenv_file(Path(settings.librechat_container_data_path) / slug / ".env")["LITELLM_API_KEY"]


def _candidates(database: str) -> list[dict]:
    """Recent conversations with at least two employee turns, newest first.

    One turn is a replay of the first answer only; the divergence this script
    exists for starts at the second.
    """
    since = datetime.now(UTC) - timedelta(days=_SAMPLE_DAYS)
    with _mongo_client() as client:
        conversations = list(
            client[database]
            .conversations.find(
                {"updatedAt": {"$gte": since}, "user": {"$type": "string"}},
                {"conversationId": 1, "user": 1, "_id": 0},
            )
            .sort("updatedAt", -1)
            .limit(_CANDIDATES)
        )
    messages = _sync_fetch_messages(database, [c["conversationId"] for c in conversations])
    out = []
    for conversation in conversations:
        turns = _turns_from_messages(messages.get(conversation["conversationId"], []))
        asks = [turn["content"] for turn in turns if turn["role"] == "user"]
        if len(asks) >= 2:
            out.append({"cid": conversation["conversationId"], "user": conversation["user"], "asks": asks})
    return out


def _pick(candidates: list[dict], profiles: dict[str, ChatProfile], count: int) -> list[Sample]:
    """One conversation per knowledge user first; more per user only when there are too few users."""
    eligible = [c for c in candidates if c["user"] in profiles]
    first_per_user: dict[str, dict] = {}
    for candidate in eligible:
        first_per_user.setdefault(candidate["user"], candidate)
    ordered = list(first_per_user.values()) + [c for c in eligible if c not in first_per_user.values()]
    return [Sample(c["cid"], c["user"], profiles[c["user"]], c["asks"]) for c in ordered[:count]]


def _is_knowledge_profile(profile: ChatProfile) -> bool:
    # General searches nothing, and Strict with an empty scope refuses every turn.
    return profile.kb_mode != "general" and profile.kb_slugs != ()


async def _sample(org: PortalOrg, count: int) -> list[Sample]:
    database = provisioning_names_for_slug(org.slug, domain=settings.domain).mongodb_database
    candidates = await asyncio.to_thread(_candidates, database)
    profiles: dict[str, ChatProfile] = {}
    async with tenant_scoped_session(org.id) as db:
        for user in dict.fromkeys(c["user"] for c in candidates):
            try:
                profile = await resolve_internal_profile(db, org, user, remember=False)
            except LibreChatIdentityError:
                continue
            if _is_knowledge_profile(profile):
                profiles[user] = profile
    return _pick(candidates, profiles, count)


async def _resample(org: PortalOrg, entries: list[dict]) -> list[Sample]:
    """Rebuild the exact samples a saved sample.json names, without asking Mongo which conversations are recent.

    _candidates() windows on updatedAt, so re-running it on a resume can see a
    different, reordered set if a conversation was touched in between — the
    saved cid/user pairs are looked up directly instead.
    """
    database = provisioning_names_for_slug(org.slug, domain=settings.domain).mongodb_database
    messages = await asyncio.to_thread(_sync_fetch_messages, database, [e["cid"] for e in entries])
    profiles: dict[str, ChatProfile] = {}
    async with tenant_scoped_session(org.id) as db:
        for user in dict.fromkeys(e["user"] for e in entries):
            profiles[user] = await resolve_internal_profile(db, org, user, remember=False)
    samples = []
    for entry in entries:
        turns = _turns_from_messages(messages.get(entry["cid"], []))
        asks = [turn["content"] for turn in turns if turn["role"] == "user"]
        samples.append(Sample(entry["cid"], entry["user"], profiles[entry["user"]], asks))
    return samples


def _read_saved_sample(out: Path, count: int, max_turns: int) -> dict | None:
    """The saved sample.json for this run, or None on a first run (nothing saved yet)."""
    path = out / "sample.json"
    if not path.exists():
        return None
    saved = json.loads(path.read_text())
    if saved["count"] != count or saved["max_turns"] != max_turns:
        raise SystemExit(
            f"{path} was sampled for count={saved['count']} max_turns={saved['max_turns']}; "
            f"this run asked for count={count} max_turns={max_turns}. Resume with the same arguments."
        )
    return saved


def _save_sample(out: Path, samples: list[Sample], count: int, max_turns: int) -> None:
    entries = [{"cid": s.cid, "user": s.librechat_user_id} for s in samples]
    (out / "sample.json").write_text(json.dumps({"count": count, "max_turns": max_turns, "entries": entries}, indent=2))


def _body(messages: list[dict]) -> dict:
    """The same request body for both paths."""
    return {"model": "klai-primary", "stream": True, "messages": messages}


async def _old_answer(client: httpx.AsyncClient, tenant_key: str, librechat_user_id: str, messages: list[dict]) -> dict:
    async def _call() -> dict:
        started = time.perf_counter()
        first_token_ms: int | None = None
        parts: list[str] = []
        async with client.stream(
            "POST",
            f"{settings.litellm_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {tenant_key}"},
            json={**_body(messages), "user": librechat_user_id},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                text = partner._chat_sse_delta_text(line.encode())
                if text:
                    first_token_ms = first_token_ms or _ms_since(started)
                    parts.append(text)
        return {"text": "".join(parts), "ttft_ms": first_token_ms, "total_ms": _ms_since(started)}

    return await sim._with_backoff("old path", _call)


_captured_signals: list[dict] = []


async def _skip_retrieval_log(*_: object, **__: object) -> None:
    return None


async def _capture_internal_turn(*, answer_signals: dict[str, Any], **_: object) -> None:
    """Stands in for ``record_internal_turn``: a replayed turn is not an employee's turn."""
    _captured_signals.append(dict(answer_signals))


async def _new_answer(org: PortalOrg, profile: ChatProfile, messages: list[dict]) -> dict:
    # A context, not a key: chat_completions only checks "chat", and the profile
    # passed in decides surface and scope. is_preview keeps the turn out of the
    # knowledge-gap backlog, as it does for widget previews.
    auth = PartnerAuthContext(
        key_id="replay",
        org_id=org.id,
        zitadel_org_id=org.zitadel_org_id,
        permissions={"chat": True},
        kb_access={},
        rate_limit_rpm=0,
        is_preview=True,
    )
    _captured_signals.clear()
    started = time.perf_counter()
    first_token_ms: int | None = None
    parts: list[str] = []
    async with tenant_scoped_session(org.id) as db:
        response = await partner.chat_completions(
            request=partner._parse_knowledge_chat_request(_body(messages)),
            http_request=Request({"type": "http", "headers": []}),
            auth=auth,
            db=db,
            profile=profile,
        )
        if not isinstance(response, StreamingResponse):
            raise TypeError("the new path answered a streaming request without a stream")
        async for chunk in response.body_iterator:
            text = partner._chat_sse_delta_text(chunk if isinstance(chunk, bytes) else str(chunk).encode())
            if text:
                first_token_ms = first_token_ms or _ms_since(started)
                parts.append(text)
    total_ms = _ms_since(started)
    if partner._pending:
        await asyncio.wait(set(partner._pending), timeout=_SIGNAL_WAIT_SECONDS)
    return {
        "text": "".join(parts),
        "ttft_ms": first_token_ms,
        "total_ms": total_ms,
        "signals": _captured_signals[-1] if _captured_signals else None,
    }


def _ms_since(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


async def _converse(
    client: httpx.AsyncClient, ask: Ask, first: str, goal: str, max_turns: int, zitadel_org_id: str
) -> list[dict]:
    """The employee's own first message, then the simulated employee, against one path."""
    messages: list[dict] = [{"role": "user", "content": first}]
    transcript = [f"Employee: {first}"]
    answers: list[dict] = []
    for turn in range(max_turns):
        answer = await ask(messages)
        answers.append({"turn": turn, "employee": messages[-1]["content"], **answer})
        messages.append({"role": "assistant", "content": answer["text"]})
        transcript.append(f"Assistant: {answer['text']}")
        if turn == max_turns - 1:
            break
        await asyncio.sleep(sim._PAUSE_BETWEEN_TURNS)
        following = await sim._model(
            client,
            _EMPLOYEE_SYSTEM,
            f"Your goal:\n{goal}\n\nThe conversation so far:\n" + "\n\n".join(transcript),
            max_tokens=120,
            temperature=0.0,
            zitadel_org_id=zitadel_org_id,
        )
        if not following or following.upper().startswith("DONE"):
            break
        messages.append({"role": "user", "content": following})
        transcript.append(f"Employee: {following}")
    return answers


def _side(answers: list[dict], turn: int) -> str:
    before = [f"Employee: {a['employee']}\nAssistant: {a['text']}" for a in answers[:turn]]
    judged = answers[turn]
    text = "CONVERSATION SO FAR:\n" + ("\n\n".join(before) or "(none)")
    text += f"\n\nANSWER:\nEmployee: {judged['employee']}\nAssistant: {judged['text']}"
    if turn + 1 < len(answers):
        after = answers[turn + 1]
        text += f"\n\nWHAT FOLLOWED:\nEmployee: {after['employee']}\nAssistant: {after['text']}"
    return text


def _parse_verdict(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    if parsed.get("better") not in ("A", "B", "tie"):
        return None
    for side in ("A", "B"):
        flags = parsed.get(side)
        if not isinstance(flags, dict) or not all(isinstance(flags.get(f), bool) for f in ("refused", "asked")):
            return None
    return parsed


async def _judge_pair(
    client: httpx.AsyncClient, goal: str, old_side: str, new_side: str, zitadel_org_id: str
) -> tuple[str, dict | None, dict | None]:
    """(verdict, old flags, new flags); both orders, and a disagreement is a tie.

    A flag counts only when both readings set it, the same rule as the verdict.
    """
    readings = []
    for first, second in ((old_side, new_side), (new_side, old_side)):
        raw = await sim._model(
            client,
            _JUDGE_SYSTEM,
            f"The employee's goal:\n{goal}\n\n=== SIDE A ===\n{first}\n\n=== SIDE B ===\n{second}",
            max_tokens=120,
            temperature=0.0,
            zitadel_org_id=zitadel_org_id,
        )
        readings.append(_parse_verdict(raw))
    old_first, new_first = readings
    if old_first is None or new_first is None:
        return "invalid", None, None
    winners = (
        {"A": "old", "B": "new", "tie": "tie"}[old_first["better"]],
        {"A": "new", "B": "old", "tie": "tie"}[new_first["better"]],
    )
    verdict = winners[0] if winners[0] == winners[1] else "tie"

    def flags(in_old_first: str, in_new_first: str) -> dict:
        return {f: old_first[in_old_first][f] and new_first[in_new_first][f] for f in ("refused", "asked")}

    return verdict, flags("A", "B"), flags("B", "A")


async def _replay_one(
    client: httpx.AsyncClient, org: PortalOrg, tenant_key: str, sample: Sample, max_turns: int, index: int
) -> list[dict]:
    zitadel_org_id = org.zitadel_org_id
    goal = (
        await sim._model(
            client, _GOAL_SYSTEM, "\n".join(sample.asks), max_tokens=80, temperature=0.0, zitadel_org_id=zitadel_org_id
        )
        or sample.asks[0]
    )
    first = sample.asks[0]

    async def ask_old(messages: list[dict]) -> dict:
        return await _old_answer(client, tenant_key, sample.librechat_user_id, messages)

    async def ask_new(messages: list[dict]) -> dict:
        return await _new_answer(org, sample.profile, messages)

    old = await _converse(client, ask_old, first, goal, max_turns, zitadel_org_id)
    new = await _converse(client, ask_new, first, goal, max_turns, zitadel_org_id)

    records = []
    for turn in range(max(len(old), len(new))):
        record: dict[str, Any] = {
            "conversation": index,
            "cid": sample.cid,
            "mode": sample.profile.kb_mode,
            "goal": goal,
            "turn": turn,
            "old": old[turn] if turn < len(old) else None,
            "new": new[turn] if turn < len(new) else None,
        }
        if record["old"] and record["new"]:
            record["verdict"], record["old_flags"], record["new_flags"] = await _judge_pair(
                client, goal, _side(old, turn), _side(new, turn), zitadel_org_id
            )
        else:
            record["verdict"] = "unpaired"
        records.append(record)
    return records


def _percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def summarize(records: list[dict]) -> dict:
    """Counts only: nothing a conversation said ends up in here."""
    turns = [r for r in records if "turn" in r]
    judged = [r for r in turns if r["verdict"] in ("new", "old", "tie")]

    def score(rows: list[dict]) -> dict:
        verdicts = Counter(r["verdict"] for r in rows)
        return {"wins": verdicts["new"], "ties": verdicts["tie"], "losses": verdicts["old"]}

    def rate(side: str, flag: str) -> dict:
        return {"count": sum(1 for r in judged if r[f"{side}_flags"][flag]), "of": len(judged)}

    def latency(side: str) -> dict:
        answers = [r[side] for r in turns if r[side]]
        ttft = [a["ttft_ms"] for a in answers if a["ttft_ms"] is not None]
        total = [a["total_ms"] for a in answers]
        return {
            "ttft_p50": _percentile(ttft, 0.5),
            "ttft_p95": _percentile(ttft, 0.95),
            "total_p50": _percentile(total, 0.5),
            "total_p95": _percentile(total, 0.95),
        }

    new_signals = [r["new"]["signals"] for r in turns if r["new"]]
    present = [s for s in new_signals if s is not None]
    return {
        "conversations": len({r["conversation"] for r in records}),
        "failed_conversations": sum(1 for r in records if "error" in r),
        "judged_pairs": len(judged),
        "invalid_pairs": sum(1 for r in turns if r["verdict"] == "invalid"),
        "unpaired_turns": sum(1 for r in turns if r["verdict"] == "unpaired"),
        "overall": score(judged),
        "per_mode": {
            mode: score([r for r in judged if r["mode"] == mode]) for mode in sorted({r["mode"] for r in judged})
        },
        "refusals": {"old": rate("old", "refused"), "new": rate("new", "refused")},
        "questions": {"old": rate("old", "asked"), "new": rate("new", "asked")},
        "latency_ms": {"old": latency("old"), "new": latency("new")},
        "new_signals": {
            "decision": dict(Counter(str(s.get("decision")) for s in present)),
            "grounding": dict(Counter(str(s.get("grounding")) for s in present)),
            "answer_plan_fired": sum(1 for s in present if s.get("planned_question")),
            "answers": len(new_signals),
            "missing": len(new_signals) - len(present),
        },
    }


def _print_summary(summary: dict) -> None:
    def pct(part: dict) -> str:
        return f"{part['count']}/{part['of']} ({round(100 * part['count'] / part['of']) if part['of'] else 0}%)"

    def wlt(score: dict) -> str:
        return f"wins {score['wins']}, ties {score['ties']}, losses {score['losses']}"

    overall = summary["overall"]
    print(
        f"\n=== {summary['conversations']} conversations, {summary['failed_conversations']} failed ===\n"
        f"pairs judged: {summary['judged_pairs']} (invalid verdict {summary['invalid_pairs']}, "
        f"one side ended earlier {summary['unpaired_turns']})\n"
        f"NEW vs OLD: {wlt(overall)} (non-tie pairs {overall['wins'] + overall['losses']}; "
        "about 47 are needed to see a 70/30 preference)"
    )
    for mode, score in summary["per_mode"].items():
        print(f"  {mode}: {wlt(score)}")
    for label, key in (("refusals", "refusals"), ("asked a question", "questions")):
        print(f"{label}: OLD {pct(summary[key]['old'])}, NEW {pct(summary[key]['new'])}")
    for side in ("old", "new"):
        lat = summary["latency_ms"][side]
        print(
            f"latency {side.upper()} ms: first token p50 {lat['ttft_p50']} p95 {lat['ttft_p95']}, "
            f"total p50 {lat['total_p50']} p95 {lat['total_p95']}"
        )
    signals = summary["new_signals"]
    print(
        f"NEW signals: decision {signals['decision']}, grounding {signals['grounding']}, "
        f"answer plan fired {signals['answer_plan_fired']}/{signals['answers']}, missing {signals['missing']}"
    )


async def main(org_slug: str, count: int, max_turns: int) -> None:
    if sim._SIMULATION_MODEL == settings.answer_grounding_model:
        raise SystemExit("The simulation may not run on the model of the grounding check it measures.")
    out = _out_dir(org_slug)
    _route_logs_to(out / "pipeline.log")
    org = await _load_org(org_slug)
    tenant_key = _tenant_litellm_key(org.slug)
    partner.record_internal_turn = _capture_internal_turn  # type: ignore[assignment]
    # The retrieval log keys feedback to the chunks of the employee's latest
    # turn; a replayed turn written there would be mistaken for theirs.
    partner.write_retrieval_log = _skip_retrieval_log  # type: ignore[assignment]

    saved = _read_saved_sample(out, count, max_turns)
    if saved is None:
        samples = await _sample(org, count)
        _save_sample(out, samples, count, max_turns)
    else:
        samples = await _resample(org, saved["entries"])

    done_cids = _done_cids(out)
    if done_cids:
        resuming = sum(1 for s in samples if s.cid in done_cids)
        print(f"{resuming} conversation(s) already in {out}, resuming the rest", flush=True)
    print(f"{len(samples)} conversations sampled from {len({s.librechat_user_id for s in samples})} users", flush=True)

    turns_path = out / "turns.jsonl"
    # Appended and flushed per conversation: a deploy that kills this process
    # mid-run must not lose the conversations that already finished.
    with turns_path.open("a") as handle:
        async with httpx.AsyncClient(timeout=180.0) as client:
            for index, sample in enumerate(samples, 1):
                if sample.cid in done_cids:
                    continue
                try:
                    conv_records = await _replay_one(client, org, tenant_key, sample, max_turns, index)
                except Exception as exc:  # one broken conversation may not stop the run
                    conv_records = [
                        {
                            "conversation": index,
                            "cid": sample.cid,
                            "mode": sample.profile.kb_mode,
                            "error": type(exc).__name__,
                        }
                    ]
                    print(f"{index}/{len(samples)} failed: {type(exc).__name__}", flush=True)
                else:
                    print(f"{index}/{len(samples)} replayed", flush=True)
                for record in conv_records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                await asyncio.sleep(sim._PAUSE_BETWEEN_CONVERSATIONS)

    records = [json.loads(line) for line in turns_path.read_text().splitlines() if line.strip()]
    summary = summarize(records)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    _print_summary(summary)
    print(f"files: {out}")


if __name__ == "__main__":
    asyncio.run(
        main(
            sys.argv[1],
            int(sys.argv[2]) if len(sys.argv) > 2 else 12,
            int(sys.argv[3]) if len(sys.argv) > 3 else 4,
        )
    )
