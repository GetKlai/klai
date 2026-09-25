"""The internal-chat replay judges fairly, counts correctly and never prints what employees wrote.

scripts/replay_internal_chat.py reads real LibreChat conversations. Everything
here is synthetic (fictional names, example.com) and every network, Mongo and
database call is replaced.
"""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import replay_internal_chat as replay

from app.api import partner
from app.services import chat_profile
from app.services.chat_profile import ChatProfile

_ORG = SimpleNamespace(id=7, slug="brightwater", zitadel_org_id="zorg-brightwater")


def _verdict(better: str) -> str:
    flags = {"refused": False, "asked": False}
    return json.dumps({"better": better, "A": flags, "B": flags})


@pytest.mark.parametrize(
    ("old_shown_first", "new_shown_first", "expected"),
    [
        ("A", "A", "tie"),  # each reading prefers whichever answer it saw first
        ("B", "A", "new"),  # both readings prefer NEW
    ],
)
async def test_pair_judge_reads_both_orders_and_a_disagreement_is_a_tie(
    monkeypatch, old_shown_first, new_shown_first, expected
):
    prompts: list[str] = []
    replies = iter([_verdict(old_shown_first), _verdict(new_shown_first)])

    async def model(_client, _system, user, **_kwargs):
        prompts.append(user)
        return next(replies)

    monkeypatch.setattr(replay.sim, "_model", model)

    verdict, _, _ = await replay._judge_pair(None, "goal", "OLD-SIDE", "NEW-SIDE", "zorg-brightwater")

    assert verdict == expected
    assert len(prompts) == 2
    assert prompts[0].index("OLD-SIDE") < prompts[0].index("NEW-SIDE")
    assert prompts[1].index("NEW-SIDE") < prompts[1].index("OLD-SIDE")


def _answer(ttft: int | None, total: int, signals: dict | None = None) -> dict:
    return {"text": "x", "employee": "y", "ttft_ms": ttft, "total_ms": total, "signals": signals}


def _pair(conversation, mode, verdict, *, old_refused=False, new_asked=False, new_signals=None):
    return {
        "conversation": conversation,
        "mode": mode,
        "turn": 0,
        "verdict": verdict,
        "old": _answer(100, 1000),
        "new": _answer(200, 2000, new_signals),
        "old_flags": {"refused": old_refused, "asked": False},
        "new_flags": {"refused": False, "asked": new_asked},
    }


def test_summary_counts_wins_ties_losses_and_rates_from_the_turns_file(tmp_path):
    records = [
        _pair(1, "strict", "new", new_signals={"decision": "answer", "grounding": "all_in_articles"}),
        _pair(1, "strict", "tie", old_refused=True, new_signals={"decision": "refusal"}),
        _pair(2, "open", "old", new_asked=True, new_signals={"decision": "answer", "planned_question": True}),
        _pair(2, "open", "new", new_signals=None),
        {**_pair(3, "open", "invalid"), "old_flags": None, "new_flags": None},
        {**_pair(3, "open", "unpaired"), "new": None},
        {"conversation": 4, "mode": "strict", "error": "HTTPStatusError"},
    ]
    turns_file = tmp_path / "turns.jsonl"
    turns_file.write_text("".join(json.dumps(r) + "\n" for r in records))

    summary = replay.summarize([json.loads(line) for line in turns_file.read_text().splitlines()])

    assert summary["overall"] == {"wins": 2, "ties": 1, "losses": 1}
    assert summary["per_mode"] == {
        "open": {"wins": 1, "ties": 0, "losses": 1},
        "strict": {"wins": 1, "ties": 1, "losses": 0},
    }
    assert (summary["judged_pairs"], summary["invalid_pairs"], summary["unpaired_turns"]) == (4, 1, 1)
    assert (summary["conversations"], summary["failed_conversations"]) == (4, 1)
    assert summary["refusals"]["old"] == {"count": 1, "of": 4}
    assert summary["questions"]["new"] == {"count": 1, "of": 4}
    assert summary["latency_ms"]["new"]["total_p50"] == 2000
    assert summary["new_signals"]["decision"] == {"answer": 2, "refusal": 1}
    assert summary["new_signals"]["answer_plan_fired"] == 1
    assert (summary["new_signals"]["answers"], summary["new_signals"]["missing"]) == (5, 2)


def _profile(user_id: str) -> ChatProfile:
    return ChatProfile(surface="internal", kb_mode="strict", user_id=user_id)


async def test_stdout_carries_counts_and_never_conversation_text(monkeypatch, tmp_path, capsys):
    private = [
        "Hoe vraag ik ouderschapsverlof aan bij Brightwater?",
        "Ik wil weten hoeveel verlofdagen Jansen nog heeft",
        "Vraag verlof aan via het formulier van Fenna de Vries",
        "Welke afdeling bedoel je, Noordhaven of Zuidkade?",
        "Afdeling Noordhaven graag",
        "fenna.devries@example.com",
    ]
    samples = [
        replay.Sample("c-1", "u-1", _profile("sub-1"), [private[0], private[1]]),
        replay.Sample("c-2", "u-2", _profile("sub-2"), [private[5], private[1]]),
    ]

    async def old_answer(_client, _key, user, _messages):
        if user == "u-2":
            raise ValueError(private[5])
        return {"text": private[2], "ttft_ms": 10, "total_ms": 30}

    async def new_answer(_org, _profile, _messages):
        return {"text": private[3], "ttft_ms": 20, "total_ms": 40, "signals": {"decision": "answer"}}

    async def model(_client, system, user, **_kwargs):
        if system == replay._GOAL_SYSTEM:
            return private[1]
        if system == replay._EMPLOYEE_SYSTEM:
            return private[4]
        side_a = user.split("=== SIDE B ===")[0]
        return _verdict("A" if private[3] in side_a else "B")  # prefers NEW in both orders

    async def load_org(_slug):
        return _ORG

    async def sample(_org, _count):
        return samples

    monkeypatch.setenv("KLAI_REPLAY_OUT", str(tmp_path / "out"))
    monkeypatch.setattr(replay, "_route_logs_to", lambda _path: None)
    monkeypatch.setattr(replay, "_load_org", load_org)
    monkeypatch.setattr(replay, "_tenant_litellm_key", lambda _slug: "sk-synthetic")
    monkeypatch.setattr(replay, "_sample", sample)
    monkeypatch.setattr(replay, "_old_answer", old_answer)
    monkeypatch.setattr(replay, "_new_answer", new_answer)
    monkeypatch.setattr(replay.sim, "_model", model)
    monkeypatch.setattr(replay.sim, "_PAUSE_BETWEEN_TURNS", 0)
    monkeypatch.setattr(replay.sim, "_PAUSE_BETWEEN_CONVERSATIONS", 0)
    monkeypatch.setattr(partner, "record_internal_turn", partner.record_internal_turn)

    await replay.main("brightwater", 2, 2)

    out = capsys.readouterr().out
    assert "NEW vs OLD: wins 2, ties 0, losses 0" in out
    assert "1 failed" in out
    for text in private:
        assert text not in out
    turns = (tmp_path / "out" / "turns.jsonl").read_text()
    assert private[2] in turns, "the transcripts belong in the output directory"


async def test_new_call_runs_with_the_internal_profile_resolved_for_the_sampled_user(monkeypatch):
    employees = {
        "u-strict": SimpleNamespace(
            zitadel_user_id="sub-strict",
            status="active",
            kb_slugs_filter=["handbook"],
            kb_retrieval_enabled=True,
            kb_personal_enabled=False,
            kb_narrow=True,
        ),
        "u-general": SimpleNamespace(
            zitadel_user_id="sub-general",
            status="active",
            kb_slugs_filter=None,
            kb_retrieval_enabled=False,
            kb_personal_enabled=False,
            kb_narrow=False,
        ),
    }

    remembered: list[bool] = []

    async def resolve_user(_db, _org, librechat_user_id, *, remember):
        remembered.append(remember)
        return employees[librechat_user_id]

    async def knowledge_access(_db, _user):
        return True

    @asynccontextmanager
    async def session(_org_id):
        yield object()

    candidates = [
        {"cid": "c-general", "user": "u-general", "asks": ["Hallo", "Nog iets"]},
        {"cid": "c-strict", "user": "u-strict", "asks": ["Waar staat het verlofbeleid?", "En voor Noordhaven?"]},
    ]
    monkeypatch.setattr(chat_profile, "resolve_librechat_user", resolve_user)
    monkeypatch.setattr(chat_profile, "has_knowledge_access", knowledge_access)
    monkeypatch.setattr(replay, "tenant_scoped_session", session)
    monkeypatch.setattr(replay, "_candidates", lambda _database: candidates)

    calls: list[dict] = []

    async def chat_completions(**kwargs):
        calls.append(kwargs)
        await partner.record_internal_turn(org_id=_ORG.id, answer_signals={"decision": "answer"})

        async def body():
            yield b'data: {"choices": [{"delta": {"content": "Zie het handboek."}}]}\n\n'

        return StreamingResponse(body(), media_type="text/event-stream")

    monkeypatch.setattr(partner, "chat_completions", chat_completions)
    monkeypatch.setattr(partner, "record_internal_turn", replay._capture_internal_turn)

    samples = await replay._sample(_ORG, 5)
    assert [s.cid for s in samples] == ["c-strict"], "a user without knowledge mode is not replayed"
    assert remembered and not any(remembered), "the replay may not write the LibreChat mapping to production"
    answer = await replay._new_answer(_ORG, samples[0].profile, [{"role": "user", "content": samples[0].asks[0]}])

    profile = calls[0]["profile"]
    assert profile.surface == "internal"
    assert profile.user_id == "sub-strict"
    assert profile.kb_slugs == ("handbook",)
    assert not calls[0]["auth"].permissions.get("internal_chat")
    assert answer["text"] == "Zie het handboek."
    assert answer["signals"] == {"decision": "answer"}


def _resume_samples() -> list[replay.Sample]:
    return [
        replay.Sample("c-1", "u-1", _profile("sub-1"), ["Vraag 1"]),
        replay.Sample("c-2", "u-2", _profile("sub-2"), ["Vraag 2"]),
    ]


def _patch_resume_run(monkeypatch, *, sample) -> None:
    async def old_answer(_client, _key, user, _messages):
        return {"text": f"OLD-{user}", "ttft_ms": 10, "total_ms": 30}

    async def new_answer(_org, _profile, _messages):
        return {"text": "NEW", "ttft_ms": 20, "total_ms": 40, "signals": None}

    async def model(_client, system, _user, **_kwargs):
        return "goal" if system == replay._GOAL_SYSTEM else _verdict("tie")

    async def load_org(_slug):
        return _ORG

    monkeypatch.setattr(replay, "_route_logs_to", lambda _path: None)
    monkeypatch.setattr(replay, "_load_org", load_org)
    monkeypatch.setattr(replay, "_tenant_litellm_key", lambda _slug: "sk-synthetic")
    monkeypatch.setattr(replay, "_sample", sample)
    monkeypatch.setattr(replay, "_old_answer", old_answer)
    monkeypatch.setattr(replay, "_new_answer", new_answer)
    monkeypatch.setattr(replay.sim, "_model", model)
    monkeypatch.setattr(replay.sim, "_PAUSE_BETWEEN_TURNS", 0)
    monkeypatch.setattr(replay.sim, "_PAUSE_BETWEEN_CONVERSATIONS", 0)


async def test_resuming_skips_done_conversations_and_appends(monkeypatch, tmp_path, capsys):
    """A deploy that kills the process mid-run must not cost the conversations already replayed.

    KLAI_REPLAY_OUT pointing at the interrupted run's own folder resumes it:
    the sampled list is unchanged, but any cid already in turns.jsonl is
    skipped instead of replayed a second time.
    """
    samples = _resume_samples()
    out_dir = tmp_path / "out"
    monkeypatch.setenv("KLAI_REPLAY_OUT", str(out_dir))

    async def sample_first_only(_org, _count):
        return samples[:1]

    # The interrupted run: only c-1 was sampled and replayed before it died.
    _patch_resume_run(monkeypatch, sample=sample_first_only)
    await replay.main("brightwater", 1, 1)
    assert (out_dir / "turns.jsonl").read_text().count("\n") == 1

    # The resumed run: same folder, both conversations sampled again.
    async def sample_both(_org, _count):
        return samples

    _patch_resume_run(monkeypatch, sample=sample_both)
    await replay.main("brightwater", 2, 1)

    out = capsys.readouterr().out
    assert "1 conversation(s) already in" in out
    assert "resuming the rest" in out

    lines = [json.loads(line) for line in (out_dir / "turns.jsonl").read_text().splitlines()]
    assert [r["cid"] for r in lines] == ["c-1", "c-2"], "c-1's line is kept, not replayed again"
    assert lines[1]["conversation"] == 2, "the resumed conversation keeps its position in the sampled list"


async def test_summary_from_an_appended_file_matches_a_single_run(monkeypatch, tmp_path):
    """turns.jsonl written across two interrupted runs must summarize the same as one clean run."""
    samples = _resume_samples()

    async def sample_both(_org, _count):
        return samples

    single_out = tmp_path / "single"
    monkeypatch.setenv("KLAI_REPLAY_OUT", str(single_out))
    _patch_resume_run(monkeypatch, sample=sample_both)
    await replay.main("brightwater", 2, 1)
    single_summary = json.loads((single_out / "summary.json").read_text())

    async def sample_first_only(_org, _count):
        return samples[:1]

    resumed_out = tmp_path / "resumed"
    monkeypatch.setenv("KLAI_REPLAY_OUT", str(resumed_out))
    _patch_resume_run(monkeypatch, sample=sample_first_only)
    await replay.main("brightwater", 1, 1)
    _patch_resume_run(monkeypatch, sample=sample_both)
    await replay.main("brightwater", 2, 1)
    resumed_summary = json.loads((resumed_out / "summary.json").read_text())

    assert resumed_summary == single_summary
