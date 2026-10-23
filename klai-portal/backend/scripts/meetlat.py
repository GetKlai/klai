"""The chat quality measuring stick (docs/architecture/chat-quality-history-and-plan.md §6, §8) as one CLI.

    meetlat run     --surface internal --org <slug> --cases <cases.jsonl> --out <rows.jsonl> [--overlay <dir>] [--days <n>]
    meetlat run     --surface widget --org <slug> --widget <widget.json> --cases <cases.jsonl> --out <rows.jsonl> [--overlay <dir>]
    meetlat retrieval --surface internal|widget --org <slug> --cases <cases.jsonl> --out <rows.jsonl> [same options as run]
    meetlat compare --cases <cases.jsonl> [--json] <round.jsonl>...

The script holds no case data. An internal case is one JSON object per line
with ``cid`` (the first 8 characters of a conversation id) and ``turn`` (the
index of the employee's turn in that conversation); the conversation itself is
read from the tenant's LibreChat database. A widget case carries its own
conversation: ``id`` and ``messages`` (the visitor's turns and the earlier
replies, ending in the turn to replay). ``run`` ignores every other field.
``compare`` also reads ``class`` or ``expect`` (the expectation class) and, for
``dekkend_antwoord``, ``variants`` (regexes that must all appear in the answer;
default android and iphone|ios). Internal classes: weigering, antwoord_kb,
dekkend_antwoord, antwoord_algemeen_gelabeld and weigering_feiten (read by
hand). Widget classes: antwoord, vraag, niet_gevonden and doorverwijzing.

``run`` replays those turns through the one chat pipeline (the internal
surface the way replay_internal_chat.py does, the widget surface through the
route itself with a preview session token, so the widget's own settings and
articles apply) and writes one row per turn. A turn after the first carries
the case its conversation's earlier turns stored, as in production, so ``run``
replays every user turn of a conversation up to its highest named turn, in
order, and writes rows for the named turns only; the earlier turns are
replayed for their memory. That memory lives in this process, keyed by
conversation (LibreChat's conversation id; the widget's preview session), and
never reaches the database. Before the first turn
it prints which files the pipeline's own modules were loaded from: in October
2026 three rounds ran the code of main instead of the candidate because a module
put its own root at the front of sys.path and shadowed the candidate overlay.
With ``--overlay`` it refuses (exit 2) unless every one of those modules comes
from under that directory.

``retrieval`` replays the same turns but stops every one of them right after its
retrieval, so no model selects or writes. For the named turns of the cases with an
expected article it asks retrieval-api for the source keys of each stage
(``debug_stages``) and writes where the article stood after search, after the rerank
and in the evidence pack the chat would have received (before its selection step), plus a summary (recall per stage,
MRR of the served list, timing of /retrieve) next to the rows. The expected article is
the case's ``articles`` (URLs); an internal case without them takes the links of the
reply recorded in the conversation, for the classes the knowledge base should answer
(antwoord, antwoord_kb, dekkend_antwoord). Other cases get a skipped row.

This script never touches sys.path. The caller sets PYTHONPATH, the candidate
overlay first and the backend root after it (docs/runbooks/meetlat.md). The
transcripts are real conversations, so ``--out`` must lie outside the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import itertools
import json
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import replay_internal_chat as replay
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import database
from app.services import answer_compose, answer_grounding, case_file, retrieval_context
from app.services.chat_profile import ChatProfile
from app.services.citations import source_url_key
from app.services.widget_audit import session_key_from_claims

# The modules whose provenance decides what a round measured.
_BANNER_MODULES = (
    "app.services.internal_turn",
    "app.services.widget_turn",
    "app.services.retrieval_context",
    "app.services.passage_selection",
    "klai_chat_prompts",
)

# A replay measures what the check decides, not whether it beat a clock on a busy
# host, so it gets a budget far above the production 4 s (widget) and 12 s (internal).
_GROUNDING_BUDGET_SECONDS = 30.0
# Every model call of a round runs on the Vibe allowance (chat-quality-history-and-plan.md section 6). On
# 23 Oct 2026 a round on klai-primary, klai-medium and klai-fast spent the Mistral workspaces' monthly limit that
# live chat shares, and every tenant's chat answered 402.
_FAST_ALIAS = "klai-ingest"
_MEDIUM_ALIAS = "klai-judge"
# The compose alias and the container name of the live retrieval-api, whose own LLM calls the portal cannot patch.
_LIVE_RETRIEVAL_HOSTS = ("retrieval-api", "klai-core-retrieval-api-1")
# The cases name conversations by prefix; a LibreChat thread can be older than
# replay_internal_chat's 90 day sampling window.
_DEFAULT_DAYS = 365
_ASK_CHARS = 400
_READING_CHARS = 260
# An answer that is only a clarifying question is no answer from the knowledge base.
_SHORT_QUESTION_CHARS = 200
# On 22 October 2026 three deploys restarted the LiteLLM gateway or portal-api in
# the middle of a round, and the turns they hit came back empty or as errors. A
# turn that meets a restart is replayed after each of these pauses before it
# becomes an error row; any other failure is a bug and becomes one at once.
_RETRY_PAUSES_SECONDS = (10, 30, 60)
_RESTART_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, ConnectionError, TimeoutError)
_RESTART_STATUSES = (502, 503, 504)
# A held widget answer waits for the writer and the checks; the route's own
# budgets are far below this.
_WIDGET_TIMEOUT_SECONDS = 120.0
_WIDGET_FIELDS = ("wgt_id", "org_id", "kb_ids", "slug", "origin")

_CLASS_FIELDS = ("class", "klasse", "expect")
# The classes whose answer should come from an article of the knowledge base.
_ANSWERED_FROM_ARTICLES = ("antwoord", "antwoord_kb", "dekkend_antwoord")
# What a turn says once its retrieval was recorded: the pipeline is cut short there.
_PROBE_REPLY = "meetlat retrieval probe"
_AUTOMATIC_CLASSES = (
    "weigering",
    "antwoord_kb",
    "dekkend_antwoord",
    "antwoord_algemeen_gelabeld",
    # The widget's classes: the kind of reply the visitor got.
    "antwoord",
    "vraag",
    "niet_gevonden",
    "doorverwijzing",
)
_READ_CLASSES = ("weigering_feiten",)
_DEFAULT_VARIANTS = ("android", "iphone|ios")
_ANSWERS = ("answer", "partial_answer")
_GENERAL_KNOWLEDGE = re.compile(r"algemene kennis|general knowledge", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s)>\]\"']+")
_AMOUNT = re.compile(r"(?:[€$£]\s?\d[\d.,]*|\d[\d.,]*\s?(?:[€$£]|(?:euro|eur|dollar)s?\b))", re.IGNORECASE)


class UsageError(Exception):
    """The run or comparison cannot start as asked; main() prints it and exits 2."""


class OverlayError(UsageError):
    """A module the pipeline runs on did not load from the overlay."""


# --- which code runs -------------------------------------------------------------------------------------------


def code_under_test() -> dict[str, str]:
    """Module name to the file it was actually loaded from."""
    # Literal imports: Semgrep's non-literal-import rule is right that a name
    # from data must not pick the module; the names here are fixed.
    import klai_chat_prompts

    from app.services import internal_turn, passage_selection, retrieval_context, widget_turn

    modules = (internal_turn, widget_turn, retrieval_context, passage_selection, klai_chat_prompts)
    return {module.__name__: str(sys.modules[module.__name__].__file__) for module in modules}


def check_overlay(modules: dict[str, str], overlay: Path) -> None:
    root = overlay.resolve()
    outside = {name: path for name, path in modules.items() if not Path(path).resolve().is_relative_to(root)}
    if outside:
        listed = ", ".join(f"{name} ({path})" for name, path in outside.items())
        raise OverlayError(
            f"refusing to run: {len(outside)} module(s) did not load from the overlay {root}: {listed}. "
            "Put the overlay first on PYTHONPATH and give it a complete app/ and klai_chat_prompts/."
        )


def _banner(modules: dict[str, str], overlay: Path | None) -> str:
    lines = ["meetlat: code under test", *(f"  {name}: {path}" for name, path in modules.items())]
    lines.append(
        f"meetlat: overlay {overlay.resolve()} (every module above must be under it)"
        if overlay
        else "meetlat: no overlay given, nothing is checked; the code above is what runs"
    )
    return "\n".join(lines)


def _git_describe(directory: Path | None) -> str | None:
    if directory is None or not (directory / ".git").exists():
        return None
    done = subprocess.run(  # noqa: S603 - fixed argument list, the directory is the operator's own overlay
        ["git", "-C", str(directory), "describe", "--always", "--dirty"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=30,
    )
    return done.stdout.strip() if done.returncode == 0 else None


# --- run -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Thread:
    """One conversation as the pipeline replays it: the full id, the employee's profile and the readable turns."""

    cid: str
    profile: ChatProfile | None
    mode: str
    turns: list[dict]


def _skip_gap_event(**_: object) -> None:
    return None


def _make_database_read_only(db: Any) -> None:
    """Replace the app's engine with one whose connections start read-only, so a replay that writes fails.

    The server setting goes in with the connection startup packet. A ``SET`` run
    from a connect listener executes inside the transaction the asyncpg adapter
    has already begun, so the first transaction would stay read-write and a
    rollback would undo the ``SET`` for the rest of the connection. Sessions are
    rebound to the new engine; nothing has connected through the old one yet.
    """
    settings = replay.settings
    db.engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=settings.db_pool_pre_ping,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
    db.AsyncSessionLocal.configure(bind=db.engine)


_captured_widget_signals: list[dict | None] = []
# The case memory of a round, in this process: what a replayed turn stored for
# the next turn of its conversation. Never the database, which is read-only
# here and holds the employees' and visitors' own cases.
_internal_cases: dict[tuple[int, str, str], case_file.StoredTurn] = {}
# Session key to the latest reply's text and its signals (case, kept passages, decision).
_widget_cases: dict[str, tuple[str, dict]] = {}


async def _capture_widget_turn(**fields: Any) -> None:
    """Stands in for ``record_widget_turn``: a replayed turn is not a visitor's turn.

    The signals dict is kept by reference, not copied: the check that runs
    after the reply completes it in place, and the row is written once the
    pending tasks are done. The reply is also the session's memory, read by
    :func:`_remembered_widget_case`.
    """
    if fields.get("role") == "assistant":
        signals = fields.get("answer_signals")
        _captured_widget_signals.append(signals)
        _widget_cases[fields["session_key"]] = (fields.get("content") or "", signals or {})


async def _remembered_widget_case(*, session_key: str, **_: object) -> case_file.StoredTurn | None:
    """Stands in for ``last_case``: the turn the round stored last in this session, as the database row reads."""
    if session_key not in _widget_cases:
        return None
    text, signals = _widget_cases[session_key]
    kept = signals.get("kept_passages")
    return case_file.StoredTurn(
        signals.get("case"), text, kept if isinstance(kept, list) else [], signals.get("decision")
    )


async def _remembered_internal_case(*, org_id: int, user_id: str, conversation_id: str) -> case_file.StoredTurn | None:
    """Stands in for ``last_internal_case``."""
    return _internal_cases.get((org_id, user_id, conversation_id))


async def _store_internal_case(
    *, org_id: int, user_id: str, conversation_id: str, case: dict | None, kept_passages: list[dict]
) -> None:
    """Stands in for ``keep_internal_case``, which deletes the row of a turn with nothing to keep."""
    if case is None and not kept_passages:
        _internal_cases.pop((org_id, user_id, conversation_id), None)
        return
    _internal_cases[(org_id, user_id, conversation_id)] = case_file.StoredTurn(case, "", kept_passages, None)


def _apply_measurement_patches() -> None:
    """Turn the production pipeline into a measuring one: nothing it does may reach an employee's or visitor's data."""
    _make_database_read_only(database)
    replay.partner.record_internal_turn = replay._capture_internal_turn  # type: ignore[assignment]
    # A replayed turn reads and stores its case in this process, never the
    # employee's or visitor's own case.
    replay.partner.last_internal_case = _remembered_internal_case  # type: ignore[assignment]
    replay.partner.keep_internal_case = _store_internal_case  # type: ignore[assignment]
    replay.partner.last_case = _remembered_widget_case  # type: ignore[assignment]
    replay.partner.record_widget_turn = _capture_widget_turn  # type: ignore[assignment]
    # The retrieval log keys feedback to the chunks of the employee's latest turn,
    # and the gap event feeds the knowledge-gap backlog: neither may see a replay.
    replay.widget_turn.write_retrieval_log = replay._skip_retrieval_log  # type: ignore[assignment]
    retrieval_context._schedule_gap_event = _skip_gap_event  # type: ignore[assignment]
    answer_grounding._CHECK_TIMEOUT_SECONDS = _GROUNDING_BUDGET_SECONDS
    answer_grounding._INTERNAL_CHECK_TIMEOUT_SECONDS = _GROUNDING_BUDGET_SECONDS
    # The request model, and every model the chat path reads from the settings: the case, the answer judge, the
    # off-topic referral and the other judges on the small alias; the selection step, the grounding check and the
    # paraphrases on the medium one. A request model other than klai-primary also keeps the internal Strict rule
    # (retrieval_context.strict_risk_model) from moving the writer to klai-medium.
    replay._REQUEST_MODEL = _FAST_ALIAS
    replay.partner._ALLOWED_MODELS = replay.partner._ALLOWED_MODELS | {_FAST_ALIAS}
    replay.settings.extraction_model = _FAST_ALIAS
    replay.settings.answer_grounding_model = _MEDIUM_ALIAS
    replay.settings.retrieval_paraphrase_model = _MEDIUM_ALIAS


def _find_conversations(database_name: str, prefixes: list[str], days: int) -> list[dict]:
    pattern = "^(?:" + "|".join(re.escape(p) for p in prefixes) + ")"
    since = datetime.now(UTC) - timedelta(days=days)
    with replay._mongo_client() as client:
        return list(
            client[database_name].conversations.find(
                {"updatedAt": {"$gte": since}, "conversationId": {"$regex": pattern}},
                {"conversationId": 1, "user": 1, "_id": 0},
            )
        )


async def _fetch_threads(org: Any, prefixes: list[str], days: int) -> dict[str, Thread]:
    """The conversations the 8-character prefixes name; a prefix that matches none or several is left out."""
    database_name = replay.provisioning_names_for_slug(org.slug, domain=replay.settings.domain).mongodb_database
    found = await asyncio.to_thread(_find_conversations, database_name, prefixes, days)
    by_prefix: dict[str, list[dict]] = {}
    for conversation in found:
        by_prefix.setdefault(conversation["conversationId"][:8], []).append(conversation)
    unique = {prefix: matches[0] for prefix, matches in by_prefix.items() if len(matches) == 1}
    messages = await asyncio.to_thread(
        replay._sync_fetch_messages, database_name, [c["conversationId"] for c in unique.values()]
    )
    profiles: dict[str, ChatProfile | None] = {}
    async with replay.tenant_scoped_session(org.id) as db:
        for user in dict.fromkeys(c["user"] for c in unique.values()):
            try:
                profiles[user] = await replay.resolve_internal_profile(db, org, user, remember=False)
            except replay.LibreChatIdentityError:
                profiles[user] = None
    threads = {}
    for prefix, conversation in unique.items():
        profile = profiles[conversation["user"]]
        turns = replay._turns_from_messages(messages.get(conversation["conversationId"], []))
        threads[prefix] = Thread(conversation["conversationId"], profile, profile.kb_mode if profile else "", turns)
    return threads


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_cases(path: Path) -> list[dict]:
    try:
        cases = _read_jsonl(path)
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read the cases file {path}: {exc}") from exc
    for number, case in enumerate(cases, 1):
        if isinstance(case.get("messages"), list):
            # A widget case: the row is keyed like an internal one, by case id
            # and the index of the replayed turn among the visitor's turns.
            messages = [m for m in case["messages"] if isinstance(m, dict)]
            user_turns = [m for m in messages if m.get("role") == "user"]
            if not (isinstance(case.get("id"), str) and case["id"] and messages and messages[-1] in user_turns):
                raise UsageError(f"{path} line {number}: a widget case needs an id and messages ending in a user turn")
            case["cid"], case["turn"] = case["id"], len(user_turns) - 1
        elif not (
            isinstance(case.get("cid"), str)
            and len(case["cid"]) == 8
            and isinstance(case.get("turn"), int)
            and case["turn"] >= 0
        ):
            raise UsageError(f"{path} line {number}: needs a cid of 8 characters and a turn index of 0 or more")
        try:
            variants = case.get("variants") or []
            if not isinstance(variants, list):
                raise TypeError("not a list")
            for variant in variants:
                re.compile(variant)
        except (re.error, TypeError) as exc:
            raise UsageError(f"{path} line {number}: variants must be a list of regexes: {exc}") from exc
    return cases


def _case_class(case: dict) -> str:
    klass = next((case[f] for f in _CLASS_FIELDS if f in case), None)
    if klass not in (*_AUTOMATIC_CLASSES, *_READ_CLASSES):
        raise UsageError(
            f"case {case['cid']} turn {case['turn']}: field class is {klass!r}, "
            f"expected one of {', '.join((*_AUTOMATIC_CLASSES, *_READ_CLASSES))}"
        )
    return klass


def _code_fingerprint() -> dict[str, Any]:
    """One sha256 (12 hex) over the bytes of every loaded ``app`` and ``klai_chat_prompts`` module, plus their count.

    The same path can hold other code after a copy, and the pipeline runs on far
    more than the five banner modules. Call it after the pipeline is imported.
    """
    digest = hashlib.sha256()
    count = 0
    for name in sorted(sys.modules):
        if name.split(".")[0] not in ("app", "klai_chat_prompts"):
            continue
        path = getattr(sys.modules[name], "__file__", None)
        if path and Path(path).is_file():
            digest.update(name.encode() + b"\0" + Path(path).read_bytes() + b"\0")
            count += 1
    return {"code_hash": digest.hexdigest()[:12], "code_files": count}


def _guard_meta(meta_path: Path, modules: dict[str, str], fingerprint: dict[str, Any], out: Path) -> None:
    """Rows from another code tree must not be topped up with rows from this one."""
    if not (meta_path.exists() and out.exists()):
        return
    previous = json.loads(meta_path.read_text())
    if previous.get("modules") != modules or any(previous.get(k) != v for k, v in fingerprint.items()):
        raise UsageError(
            f"refusing to resume: the rows in {out} were produced with other code than this run loads "
            f"(see {meta_path}). Use a new --out."
        )


def _is_restart(exc: BaseException) -> bool:
    """Whether the exception, or one it was raised from, is a connection that was gone or timed out.

    Only what fails before the stream starts raises out of the replay (a database
    or service connection); a gateway failure arrives in the stream (``_restart_behind``).
    """
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, _RESTART_ERRORS):
            return True
        current = current.__cause__
    return False


def _restart_behind(answer: dict) -> str | None:
    """Why an answer that did come back measured a restart instead of the pipeline, or None.

    A gateway that went away mid-turn ends the stream with an error frame, often
    after part of the text. A selection step that got no verdict from the
    gateway twice turns Strict into the fixed refusal. The internal chat turns a
    lost knowledge base into a refusal or a general answer and names the failure
    in ``retrieval_failure``: ``HTTP <status>`` or the httpx error's class name.
    A persistent cause still ends as an error row after the retries.
    """
    if answer.get("stream_error"):
        return f"stream error {answer['stream_error']}"
    if answer.get("status", 200) in _RESTART_STATUSES:
        return f"HTTP {answer['status']}"
    if answer.get("status", 200) != 200:
        return None
    if not (answer.get("text") or "").strip():
        return "empty_answer"
    signals = answer.get("signals") or {}
    selection = signals.get("selection")
    if isinstance(selection, dict) and selection.get("failed"):
        return f"selection {selection['failed']}"
    failure = signals.get("retrieval_failure")
    if not failure:
        return None
    if failure.startswith("HTTP "):
        restart = int(failure.removeprefix("HTTP ")) in _RESTART_STATUSES
    else:
        error = getattr(httpx, failure, None)
        restart = isinstance(error, type) and issubclass(error, _RESTART_ERRORS)
    return f"retrieval {failure}" if restart else None


async def _replayed(row: dict, attempt_turn: Callable[[], Awaitable[dict]], keep: tuple[str, ...]) -> dict:
    """The row with the answer's ``keep`` fields, after replaying a turn that met a restart; else an error row."""
    attempts = len(_RETRY_PAUSES_SECONDS) + 1
    failure = ""
    for attempt in range(1, attempts + 1):
        try:
            answer = await attempt_turn()
        except Exception as exc:  # one broken turn may not stop the round
            if not _is_restart(exc):
                return {**row, "error": type(exc).__name__}
            failure = type(exc).__name__
        else:
            restart = _restart_behind(answer)
            if restart is None:
                if answer.get("status", 200) != 200:
                    return {**row, "error": f"HTTP {answer['status']}"}
                return {**row, **{field: answer[field] for field in keep}}
            failure = restart
        if attempt == attempts:
            break
        pause = _RETRY_PAUSES_SECONDS[attempt - 1]
        print(
            f"  {row['cid']} turn {row['u']}: {failure} on attempt {attempt} of {attempts}, replaying in {pause} s",
            flush=True,
        )
        await asyncio.sleep(pause)
    return {**row, "error": failure}


def _from_the_same_memory(
    store: dict, key: Any, attempt: Callable[[], Awaitable[dict]]
) -> Callable[[], Awaitable[dict]]:
    """``attempt``, starting each time from the memory the turn found.

    The route stores a turn's case before its answer has fully arrived, so an
    attempt that met a restart has already replaced the case of the turn
    before; a retry that read it would build on its own failed attempt.
    """
    before = store.get(key)

    async def again() -> dict:
        if before is None:
            store.pop(key, None)
        else:
            store[key] = before
        return await attempt()

    return again


async def _replay_turn(org: Any, thread: Thread, case: dict) -> dict:
    """One internal row. Index k is the position in the readable turns, u the position among the employee's turns."""
    user_turns = [k for k, turn in enumerate(thread.turns) if turn["role"] == "user"]
    u = case["turn"]
    if u >= len(user_turns):
        return {"cid": case["cid"], "u": u, "error": "turn_out_of_range"}
    k = user_turns[u]
    messages = [{"role": t["role"], "content": t["content"]} for t in thread.turns[: k + 1]]
    row = {"cid": case["cid"], "k": k, "u": u, "mode": thread.mode, "ask": messages[-1]["content"][:_ASK_CHARS]}
    profile = thread.profile
    assert profile is not None and profile.user_id is not None, "replay_internal skips a thread without an employee"
    return await _replayed(
        row,
        _from_the_same_memory(
            _internal_cases,
            (org.id, profile.user_id, thread.cid),
            lambda: replay._new_answer(org, profile, messages, thread.cid),
        ),
        ("text", "signals", "total_ms"),
    )


async def _replay_internal_conversation(
    org: Any, thread: Thread, cases: list[dict], expected: dict[int, list[str]] | None = None
) -> AsyncIterator[dict]:
    """The rows of the named turns of one conversation, after every turn before them has stored its case.

    A row after an earlier turn that failed carries ``prefix_errors``: it was
    measured without the memory that turn would have stored. With ``expected``
    (a retrieval round) every turn is cut short after its retrieval, the earlier
    ones too: the case they store does not depend on the reply, and no model
    needs to write one. The named turns' rows say where the expected articles
    stood (:func:`_stage_row`).
    """
    named = {case["turn"] for case in cases}
    from_reply = {case["turn"] for case in cases if "articles" not in case}
    failed: list[dict] = []
    for u in range(max(named) + 1):
        if expected is not None:
            _arm()
        row = await _replay_turn(org, thread, {"cid": cases[0]["cid"], "turn": u})
        if expected is not None:
            record = _take()
            if u in named:
                row = _stage_row(row, expected[u], record, kb_hosts_only=u in from_reply)
        if u in named:
            yield {**row, "prefix_errors": list(failed)} if failed else row
        if "error" in row:
            failed.append({"u": u, "error": row["error"]})


def _read_widget(path: Path) -> dict:
    """The widget a round replays as: its id, org, knowledge bases, tenant slug and allowed origin."""
    try:
        widget = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read the widget file {path}: {exc}") from exc
    if not isinstance(widget, dict) or any(field not in widget for field in _WIDGET_FIELDS):
        raise UsageError(f"{path}: needs the fields {', '.join(_WIDGET_FIELDS)}")
    return widget


def _widget_body(messages: list[dict]) -> dict:
    return {"model": _FAST_ALIAS, "stream": False, "messages": messages}


async def _widget_answer(widget: dict, messages: list[dict], session_id: str) -> dict:
    """One widget turn through the route itself, as the browser sends it: a preview session token, the answer held.

    Held (not streamed) so the reply is complete when the response is; the
    signals are read after the tasks the route left behind (the record and
    the check after the reply) are done. The turns of one conversation share
    ``session_id``, which the route turns into the session key its case
    memory is kept under.
    """
    from app.main import app
    from app.services.widget_auth import generate_session_token

    token = generate_session_token(
        widget["wgt_id"],
        widget["org_id"],
        widget["kb_ids"],
        replay.settings.widget_jwt_secret,
        widget["slug"],
        is_preview=True,
        session_id=session_id,
    )
    _captured_widget_signals.clear()
    started = time.perf_counter()
    # The ASGI transport runs the route in this process and honours no client
    # timeout, so the deadline is kept here; a turn that hangs is a restart.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://meetlat") as client:
        response = await asyncio.wait_for(
            client.post(
                "/partner/v1/chat/completions",
                json=_widget_body(messages),
                headers={"Authorization": f"Bearer {token}", "Origin": widget["origin"]},
            ),
            _WIDGET_TIMEOUT_SECONDS,
        )
    total_ms = round((time.perf_counter() - started) * 1000)
    pending = {*replay.partner._pending, *answer_compose._pending_turn_tasks}
    if pending:
        await asyncio.wait(pending, timeout=replay._SIGNAL_WAIT_SECONDS)
    message = response.json()["choices"][0]["message"] if response.status_code == 200 else {}
    return {
        "status": response.status_code,
        "text": message.get("content") or "",
        "sources": [source.get("url") for source in message.get("sources") or []],
        "appointment": bool(message.get("escalation")),
        "signals": _captured_widget_signals[-1] if _captured_widget_signals else None,
        "total_ms": total_ms,
    }


async def _replay_widget_turn(widget: dict, case: dict, expected: list[str] | None = None) -> dict:
    """One widget row: the case's own conversation, every earlier visitor turn first for its memory, then the latest.

    The history is the case's own, so the turn the route stored stands for the
    reply in that history: the route only reads a stored case that belongs
    to the conversation's last answer (``case_file.stored_case_for``), and the
    replayed reply is not that answer. The row carries ``prefix_errors`` when
    an earlier turn failed, as the internal rows do.
    """
    messages = [{"role": m["role"], "content": m["content"]} for m in case["messages"]]
    users = [k for k, message in enumerate(messages) if message["role"] == "user"]
    session_id = uuid.uuid4().hex
    session_key = session_key_from_claims(org_id=widget["org_id"], wgt_id=widget["wgt_id"], jti=session_id)
    assert session_key is not None, "_read_widget requires org_id and wgt_id"
    failed: list[dict] = []
    for u, (k, following) in enumerate(itertools.pairwise(users)):
        if expected is not None:
            _arm()
        earlier = await _replayed(
            {"cid": case["cid"], "u": u},
            _from_the_same_memory(
                _widget_cases, session_key, lambda k=k: _widget_answer(widget, messages[: k + 1], session_id)
            ),
            (),
        )
        if expected is not None:
            _take()
        if "error" in earlier:
            failed.append({"u": u, "error": earlier["error"]})
        if session_key in _widget_cases:
            _widget_cases[session_key] = (
                case_file.last_assistant_message(messages[:following]),
                _widget_cases[session_key][1],
            )
    row = {"cid": case["cid"], "u": case["turn"], "ask": messages[-1]["content"][:_ASK_CHARS]}
    if expected is not None:
        _arm()
    measured = await _replayed(
        row,
        _from_the_same_memory(_widget_cases, session_key, lambda: _widget_answer(widget, messages, session_id)),
        ("text", "sources", "appointment", "signals", "total_ms"),
    )
    if expected is not None:
        measured = _stage_row(measured, expected, _take())
    return {**measured, "prefix_errors": failed} if failed else measured


def _rows_inside_checkout(out: Path) -> bool:
    """True when ``out`` lies inside a git checkout: rows hold real conversations and a checkout has a public remote.

    An overlay on the production host (``/out/<overlay>``) has no ``.git``
    and is not a repository, so rows may sit next to it.
    """
    root = replay.REPO_ROOT
    return (root / ".git").exists() and (out == root or root in out.parents)


async def _replayer(
    args: argparse.Namespace, widget: dict | None, todo: list[dict]
) -> Callable[[list[dict]], AsyncIterator[dict]]:
    """How the cases of one conversation are replayed: a widget case as the widget, an internal case as its employee.

    A retrieval round replays only the cases that name an expected article; the others get a skipped row.
    """
    retrieving = args.command == "retrieval"
    if widget is not None:

        async def replay_widget(cases: list[dict]) -> AsyncIterator[dict]:
            for case in cases:
                expected = _expected_sources(case) if retrieving else None
                if retrieving and not expected:
                    yield _skipped_row(case)
                else:
                    yield await _replay_widget_turn(widget, case, expected)

        return replay_widget
    org = await replay._load_org(args.org)
    threads = await _fetch_threads(org, sorted({c["cid"] for c in todo}), args.days)

    async def replay_internal(cases: list[dict]) -> AsyncIterator[dict]:
        thread = threads.get(cases[0]["cid"])
        if thread is None or thread.profile is None:
            for case in cases:
                yield {"cid": case["cid"], "u": case["turn"], "error": "conversation_or_profile_not_found"}
            return
        expected = (
            {c["turn"]: _expected_sources(c, _recorded_reply(thread, c["turn"])) for c in cases} if retrieving else None
        )
        if expected is not None:
            for case in cases:
                if not expected[case["turn"]]:
                    yield _skipped_row(case)
            cases = [case for case in cases if expected[case["turn"]]]
        if not cases:
            return
        async for row in _replay_internal_conversation(org, thread, cases, expected):
            yield row

    return replay_internal


def _conversations(todo: list[dict]) -> list[list[dict]]:
    """The cases grouped by conversation, in the order the file names them, each group by turn."""
    groups: dict[str, list[dict]] = {}
    for case in todo:
        groups.setdefault(case["cid"], []).append(case)
    return [sorted(group, key=lambda case: case["turn"]) for group in groups.values()]


async def _run(args: argparse.Namespace) -> int:
    if urlparse(replay.settings.knowledge_retrieve_url).hostname in _LIVE_RETRIEVAL_HOSTS:
        raise UsageError(
            "refusing to run: KNOWLEDGE_RETRIEVE_URL is the live retrieval-api, which rewrites follow-up questions "
            "on klai-fast, a key live chat shares. Start a one-off with deploy/scripts/retrieval-api-oneoff.sh "
            "and point KNOWLEDGE_RETRIEVE_URL at it."
        )
    if args.surface == "widget" and args.widget is None:
        raise UsageError("--surface widget needs --widget <json> with " + ", ".join(_WIDGET_FIELDS))
    widget = _read_widget(args.widget) if args.surface == "widget" else None
    if widget is not None:
        # The route and its middleware are part of what a widget round measures,
        # so they are loaded before the code fingerprint; importing the app also
        # configures logging, which the routing below then replaces.
        importlib.import_module("app.main")
    all_cases = _read_cases(args.cases)
    # A cases file may span organisations; a case without an org field is for every org.
    cases = [c for c in all_cases if c.get("org", args.org) == args.org]
    overlay: Path | None = args.overlay
    modules = code_under_test()
    banner = _banner(modules, overlay)
    print(banner, flush=True)
    if overlay is not None:
        if not overlay.is_dir():
            raise UsageError(f"refusing to run: the overlay {overlay} is not a directory")
        check_overlay(modules, overlay)
    out: Path = args.out.resolve()
    if _rows_inside_checkout(out):
        raise UsageError("refusing to run: --out must be outside the repository, the rows hold real conversations")
    meta_path = out.with_name(out.name + ".meta.json")
    fingerprint = _code_fingerprint()
    _guard_meta(meta_path, modules, fingerprint, out)
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    meta = {
        "banner": banner,
        "modules": modules,
        **fingerprint,
        "overlay": str(overlay.resolve()) if overlay else None,
        "overlay_git": _git_describe(overlay),
        "started_at": datetime.now(UTC).isoformat(),
        "command": args.command,
        "org": args.org,
        "surface": args.surface,
        "cases": len(cases),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    done = {(r["cid"], r["u"]) for r in _read_jsonl(out)} if out.exists() else set()
    todo = [c for c in cases if (c["cid"], c["turn"]) not in done]
    print(
        f"{len(cases)} of {len(all_cases)} cases are for this org, {len(done)} already in {out}, replaying {len(todo)}",
        flush=True,
    )
    if not todo:
        return _finish(args, out)

    _apply_measurement_patches()
    if args.command == "retrieval":
        _install_retrieval_probe()
    replay._route_logs_to(out.with_name(out.name + ".pipeline.log"))
    replay_conversation = await _replayer(args, widget, todo)
    errors = number = 0
    with out.open("a") as handle:
        for cases in _conversations(todo):
            async for row in replay_conversation(cases):
                number += 1
                errors += "error" in row
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"{number}/{len(todo)} {'failed: ' + row['error'] if 'error' in row else 'replayed'}", flush=True)
    print(f"files: {out} ({errors} error row(s))")
    return _finish(args, out)


# --- retrieval -------------------------------------------------------------------------------------------------

# Set by the replay around each turn of a retrieval round: whether the probe records, and what the turn's retrieval left.
_probe: dict[str, Any] = {"armed": False, "record": None}


def _arm() -> None:
    _probe.update(armed=True, record=None)


def _take() -> dict | None:
    record = _probe["record"]
    _probe.update(armed=False, record=None)
    return record


def _install_retrieval_probe() -> None:
    """Cut an armed turn short after its retrieval and keep what the retrieval returned.

    ``_call_retrieval`` asks retrieval-api for its ``debug_stages`` and times the call;
    ``_choose_and_prompt`` (the selection step, the gap event and the prompt) is replaced
    by a stub that keeps the passages the chat would have received. Every way out of
    retrieval, also the turns that search nothing (``_without_search``) and a failed call,
    ends in :func:`_end_turn`, so no model writes. A turn that is not armed runs unchanged.
    """
    call, choose = retrieval_context._call_retrieval, retrieval_context._choose_and_prompt
    without_search = retrieval_context._without_search

    async def probed_call(t: Any, retrieve_body: dict) -> Any:
        if not _probe["armed"]:
            return await call(t, retrieve_body)
        started = time.perf_counter()
        result = await call(t, {**retrieve_body, "debug_stages": True})
        if isinstance(result, dict):
            _probe["record"] = {
                "top_k": retrieve_body["top_k"],
                "retrieve_ms": round((time.perf_counter() - started) * 1000),
                "api_ms": (result.get("metadata") or {}).get("retrieval_ms"),
                "stages": result.get("debug_stages"),
            }
        else:
            _probe["record"] = {"failed": True}
            _end_turn(t)
        return result

    async def probed_choose(t: Any, found: Any) -> Any:
        if not _probe["armed"]:
            return await choose(t, found)
        if _probe["record"] is not None:
            _probe["record"]["served"] = _distinct_keys(chunk.get("source_url") for chunk in found.chunks)
        _end_turn(t)
        return [], "", [], False

    def probed_without_search(t: Any, **kwargs: Any) -> Any:
        result = without_search(t, **kwargs)
        if _probe["armed"] and result is not None:
            _end_turn(t)
        return result

    retrieval_context._without_search = probed_without_search  # type: ignore[assignment]
    retrieval_context._call_retrieval = probed_call  # type: ignore[assignment]
    retrieval_context._choose_and_prompt = probed_choose  # type: ignore[assignment]


def _end_turn(t: Any) -> None:
    """Route an armed turn to a fixed reply on both surfaces, so no writer runs.

    The internal chat answers a ``refusal`` in code. The widget never reads it: it answers
    ``not_in_passages`` with its fixed "not found", and ``selection_failed`` keeps even a
    request for a person (which the phrase detector decides) from reaching the writer.
    """
    t.turn.refusal = lambda _language: _PROBE_REPLY
    t.turn.not_in_passages = True
    t.turn.selection_failed = True


def _distinct_keys(urls: Iterable[Any]) -> list[str]:
    """The source keys of the URLs, each once in the order seen, normalised the way the evidence pack keys a source."""
    return list(dict.fromkeys(key for key in map(source_url_key, urls) if key))


def _expected_sources(case: dict, recorded_reply: str = "") -> list[str]:
    """The source keys of the articles the case expects its answer from; empty when it names none.

    Only the classes the knowledge base should answer expect an article. A case
    names them in ``articles`` (the widget cases do, an empty list meaning none);
    an internal case without that field falls back to the links of the reply
    recorded in the conversation.
    """
    if _case_class(case) not in _ANSWERED_FROM_ARTICLES:
        return []
    urls = case["articles"] if "articles" in case else [url.rstrip(".,;") for url in _URL.findall(recorded_reply)]
    return _distinct_keys(urls)


def _recorded_reply(thread: Thread, u: int) -> str:
    """The reply that followed the employee's u-th turn in the recorded conversation."""
    user_turns = [k for k, turn in enumerate(thread.turns) if turn["role"] == "user"]
    following = thread.turns[user_turns[u] + 1 : user_turns[u] + 2] if u < len(user_turns) else []
    return following[0]["content"] if following and following[0]["role"] == "assistant" else ""


def _skipped_row(case: dict) -> dict:
    return {"cid": case["cid"], "u": case["turn"], "skipped": "no_expected_source"}


_MISS: dict[str, Any] = {"search": False, "rerank": False, "served": None}


def stage_hits(expected: list[str], stages: dict[str, list[str]], served: list[str]) -> dict:
    """Whether an expected source is among the candidates after search and the reranker's output; its best served rank."""
    wanted = set(expected)
    ranks = [rank for rank, key in enumerate(served, 1) if key in wanted]
    return {
        "search": bool(wanted & set(stages.get("search", ()))),
        "rerank": bool(wanted & set(stages.get("rerank", ()))),
        "served": ranks[0] if ranks else None,
    }


def _stage_row(row: dict, expected: list[str], record: dict | None, *, kb_hosts_only: bool = False) -> dict:
    """The retrieval row of a replayed turn: where the expected articles stood, and the timing of /retrieve.

    ``kb_hosts_only`` is for expected links read from a recorded reply, which can also hold
    web search citations and product URLs: only links on a host the retrieval itself returned
    for this turn count, and a case left with none expects no article.
    """
    kept = {field: row[field] for field in ("cid", "k", "u", "mode", "ask", "error") if field in row}
    if "error" in kept:
        return kept
    if kb_hosts_only and record is not None and record.get("stages") is not None:
        seen = {
            urlparse(key).netloc
            for key in [*(record.get("served") or ()), *itertools.chain.from_iterable(record["stages"].values())]
        }
        expected = [key for key in expected if urlparse(key).netloc in seen]
        if not expected:
            return {"cid": row["cid"], "u": row["u"], "skipped": "no_expected_source"}
    # The case expects an article, so a turn that lost its knowledge base or did not search
    # at all (a thank-you, a question about Klai itself) is a miss at every stage.
    if record is not None and record.get("failed"):
        return {**kept, **_MISS, "failed": True}
    if record is None:
        return {**kept, **_MISS, "not_searched": True}
    if record["stages"] is None:
        # An older retrieval-api answers without the field; that is not a miss.
        return {**kept, "error": "retrieval_api_without_debug_stages"}
    return {
        **kept,
        "expected": len(expected),
        "top_k": record["top_k"],
        **stage_hits(expected, record["stages"], record["served"]),
        "search_n": len(record["stages"].get("search", ())),
        "retrieve_ms": record["retrieve_ms"],
        "api_ms": record["api_ms"],
    }


def summarize_stages(rows: list[dict]) -> dict:
    """Recall at each stage, MRR of the served list and the timing of /retrieve over the rows that expect an article.

    A turn that failed or did not search counts as a miss; ``skipped`` are the cases that
    expect no article and ``errors`` the turns no retrieval answer could be had for.
    """
    measured = [row for row in rows if "search" in row]

    def share(hit: Callable[[dict], bool]) -> float | None:
        return round(sum(1 for row in measured if hit(row)) / len(measured), 3) if measured else None

    def timing(field: str) -> dict:
        values = [row[field] for row in measured if row.get(field) is not None]
        return {"p50": replay._percentile(values, 0.5), "p95": replay._percentile(values, 0.95)}

    return {
        "cases": len(measured),
        "failed": sum(1 for row in measured if row.get("failed")),
        "not_searched": sum(1 for row in measured if row.get("not_searched")),
        "skipped": sum(1 for row in rows if row.get("skipped") == "no_expected_source"),
        "errors": sum(1 for row in rows if "error" in row),
        "recall_search": share(lambda row: row["search"]),
        "recall_rerank": share(lambda row: row["rerank"]),
        "recall_served": share(lambda row: row["served"] is not None),
        "mrr_served": round(sum(1 / row["served"] for row in measured if row["served"]) / len(measured), 3)
        if measured
        else None,
        "retrieve_ms": timing("retrieve_ms"),
        "api_ms": timing("api_ms"),
    }


def _finish(args: argparse.Namespace, out: Path) -> int:
    """A retrieval round ends with its summary, printed and written next to the rows."""
    if args.command == "retrieval":
        summary = summarize_stages(_read_jsonl(out))
        out.with_name(out.name + ".summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


# --- compare ---------------------------------------------------------------------------------------------------


def _signals(row: dict | None) -> dict:
    return (row or {}).get("signals") or {}


def _text(row: dict | None) -> str:
    return ((row or {}).get("text") or "").strip()


def _is_short_question(text: str) -> bool:
    return text.endswith("?") and len(text) <= _SHORT_QUESTION_CHARS


def _names_variants(text: str, variants: list[str]) -> bool:
    return all(re.search(rf"\b(?:{variant})\b", text, re.IGNORECASE) for variant in variants)


def _verdict(klass: str, case: dict, row: dict | None) -> bool:
    """Whether the row meets the expectation class; a case without a row never does."""
    signals, text = _signals(row), _text(row)
    if row is None or "error" in row or not text:
        return False
    decision, sources = signals.get("decision"), signals.get("sources_count") or 0
    if klass == "weigering":
        return decision == "refusal"
    if klass == "antwoord_kb":
        return decision in _ANSWERS and sources >= 1 and not _is_short_question(text)
    if klass == "dekkend_antwoord":
        return (
            decision in _ANSWERS
            and sources >= 1
            and not _is_short_question(text)
            and _names_variants(text, case.get("variants") or list(_DEFAULT_VARIANTS))
        )
    # The widget's classes, as the earlier private comparison counted them: a
    # reply that is not a referral, not a question and not a refusal answered.
    if klass == "doorverwijzing":
        return decision == "off_topic"
    if klass == "vraag":
        return _is_short_question(text)
    if klass == "niet_gevonden":
        return decision == "refusal" or bool(signals.get("refused"))
    if klass == "antwoord":
        return decision not in ("off_topic", "refusal") and not signals.get("refused") and not _is_short_question(text)
    return decision == "general_knowledge" or (decision in _ANSWERS and bool(_GENERAL_KNOWLEDGE.search(text)))


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        return sum(_count(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return 1 if value else 0


def _round_summary(name: str, rows: dict[tuple[str, int], dict], cases: list[dict]) -> dict:
    verdicts: dict[str, dict[str, int]] = {}
    to_read = 0
    for case in cases:
        klass = _case_class(case)
        if klass in _READ_CLASSES:
            to_read += 1
            continue
        score = verdicts.setdefault(klass, {"ok": 0, "of": 0})
        score["of"] += 1
        score["ok"] += _verdict(klass, case, rows.get((case["cid"], case["turn"])))
    answered = [r for r in rows.values() if "error" not in r]
    unsupported = [s["unsupported"] for s in map(_signals, answered) if s.get("unsupported") is not None]
    hard_facts = [_count(s["hard_facts_outside_kb"]) for s in map(_signals, answered) if s.get("hard_facts_outside_kb")]
    latencies = [r["total_ms"] for r in answered if r.get("total_ms") is not None]
    selection = Counter(
        sel.get("verdict") or ("failed" if sel.get("failed") else "unknown")
        for sel in (_signals(r).get("selection") for r in answered)
        if isinstance(sel, dict)
    )
    return {
        "round": name,
        "verdicts": verdicts,
        "total": {"ok": sum(v["ok"] for v in verdicts.values()), "of": sum(v["of"] for v in verdicts.values())},
        "to_read": to_read,
        "decisions": dict(Counter(str(_signals(r).get("decision")) for r in answered)),
        "unsupported": {
            "sum": sum(unsupported),
            "answers_with": sum(1 for n in unsupported if n),
            "of": len(unsupported),
        },
        "hard_facts": {"answers": len(hard_facts), "facts": sum(hard_facts)},
        "median_total_ms": round(statistics.median(latencies)) if latencies else None,
        "errors_or_empty": sum(1 for r in rows.values() if "error" in r or not _text(r)),
        # Rows measured after an earlier turn of their conversation failed, so without its memory.
        "prefix_errors": sum(1 for r in rows.values() if r.get("prefix_errors")),
        "selection": dict(selection),
    }


def _contacts(text: str) -> dict[str, list[str]]:
    return {
        "emails": _EMAIL.findall(text),
        "amounts": [a.strip().rstrip(".,") for a in _AMOUNT.findall(text)],
        "urls": [u.rstrip(".,;") for u in _URL.findall(text)],
    }


def _reading(cases: list[dict], rounds: dict[str, dict[tuple[str, int], dict]]) -> list[dict]:
    reading = []
    for case in cases:
        klass = _case_class(case)
        if klass not in (*_READ_CLASSES, "dekkend_antwoord"):
            continue
        entries = []
        for name, rows in rounds.items():
            row = rows.get((case["cid"], case["turn"]))
            signals = _signals(row)
            entries.append(
                {
                    "round": name,
                    "decision": signals.get("decision")
                    if row and "error" not in row
                    else "error"
                    if row
                    else "missing",
                    "sources": signals.get("sources_count"),
                    "unsupported": signals.get("unsupported"),
                    "hard_facts": _count(signals.get("hard_facts_outside_kb")),
                    "text": _text(row)[:_READING_CHARS],
                    "contacts": _contacts(_text(row)),
                }
            )
        reading.append({"cid": case["cid"], "turn": case["turn"], "class": klass, "rounds": entries})
    return reading


def build_report(cases: list[dict], round_paths: list[Path]) -> dict:
    rounds = {}
    for path in round_paths:
        try:
            rows = {(r["cid"], r["u"]): r for r in _read_jsonl(path)}
        except (OSError, ValueError, KeyError) as exc:
            raise UsageError(f"cannot read the round {path}: {exc}") from exc
        if str(path) in rounds:
            raise UsageError(f"the round {path} is given twice")
        rounds[str(path)] = rows
    return {
        "rounds": [_round_summary(name, rows, cases) for name, rows in rounds.items()],
        "reading": _reading(cases, rounds),
    }


def _format_report(report: dict) -> str:
    lines = []
    for summary in report["rounds"]:
        parts = [f"{klass} {v['ok']}/{v['of']}" for klass, v in summary["verdicts"].items()]
        if summary["to_read"]:
            parts.append(f"weigering_feiten {summary['to_read']} te lezen")
        total = summary["total"]
        lines.append(f"{summary['round']}: {' | '.join(parts)} | totaal {total['ok']}/{total['of']}")
        unsupported, facts = summary["unsupported"], summary["hard_facts"]
        lines.append(
            f"  decisions {summary['decisions']}; unsupported {unsupported['sum']} in {unsupported['answers_with']} "
            f"of {unsupported['of']} answers; hard facts outside kb {facts['facts']} in {facts['answers']} answers; "
            f"median total_ms {summary['median_total_ms']}; error or empty rows {summary['errors_or_empty']}; "
            f"rows with prefix errors {summary['prefix_errors']}"
            + (f"; selection {summary['selection']}" if summary["selection"] else "")
        )
    for case in report["reading"]:
        lines += ["", f"== {case['cid']} turn {case['turn']} [{case['class']}]"]
        for entry in case["rounds"]:
            lines.append(
                f"  {entry['round']}: {entry['decision']}, sources {entry['sources']}, "
                f"unsupported {entry['unsupported']}, hard facts {entry['hard_facts']}"
            )
            lines.append(f"    {entry['text']}")
            found = {kind: values for kind, values in entry["contacts"].items() if values}
            if found:
                lines.append("    " + "; ".join(f"{kind}: {', '.join(values)}" for kind, values in found.items()))
    return "\n".join(lines)


def _compare(args: argparse.Namespace) -> int:
    report = build_report(_read_cases(args.cases), args.rounds)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _format_report(report))
    return 0


# --- command line ----------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="meetlat", description=(__doc__ or "").split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("run", "replay the cases through the chat pipeline"),
        ("retrieval", "replay only the retrieval of the cases that name an expected article, per stage"),
    ):
        replaying = commands.add_parser(name, help=text)
        replaying.add_argument("--surface", choices=("internal", "widget"), required=True)
        replaying.add_argument("--org", required=True, help="org slug")
        replaying.add_argument("--cases", type=Path, required=True)
        replaying.add_argument("--out", type=Path, required=True)
        replaying.add_argument(
            "--overlay", type=Path, help="directory with the candidate code; every pipeline module must load from it"
        )
        replaying.add_argument(
            "--days", type=int, default=_DEFAULT_DAYS, help="only conversations updated within this many days"
        )
        replaying.add_argument(
            "--widget",
            type=Path,
            help="json with " + ", ".join(_WIDGET_FIELDS) + "; the widget a --surface widget round replays as",
        )
    compare = commands.add_parser("compare", help="classify and compare rounds")
    compare.add_argument("--cases", type=Path, required=True)
    compare.add_argument("--json", action="store_true")
    compare.add_argument("rounds", nargs="+", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _compare(args) if args.command == "compare" else asyncio.run(_run(args))
    except UsageError as exc:
        print(f"meetlat: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
