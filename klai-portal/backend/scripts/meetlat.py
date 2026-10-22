"""The chat quality measuring stick (docs/architecture/chat-quality-history-and-plan.md §6, §8) as one CLI.

    meetlat run     --surface internal --org <slug> --cases <cases.jsonl> --out <rows.jsonl> [--overlay <dir>] [--days <n>]
    meetlat run     --surface widget --org <slug> --widget <widget.json> --cases <cases.jsonl> --out <rows.jsonl> [--overlay <dir>]
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
articles apply) and writes one row per turn. Before the first turn
it prints which files the pipeline's own modules were loaded from: in October
2026 three rounds ran the code of main instead of the candidate because a module
put its own root at the front of sys.path and shadowed the candidate overlay.
With ``--overlay`` it refuses (exit 2) unless every one of those modules comes
from under that directory.

This script never touches sys.path. The caller sets PYTHONPATH, the candidate
overlay first and the backend root after it (docs/runbooks/meetlat.md). The
transcripts are real conversations, so ``--out`` must lie outside the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import replay_internal_chat as replay
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import database
from app.services import answer_compose, answer_grounding, retrieval_context
from app.services.chat_profile import ChatProfile

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


async def _capture_widget_turn(**fields: Any) -> None:
    """Stands in for ``record_widget_turn``: a replayed turn is not a visitor's turn.

    The signals dict is kept by reference, not copied: the check that runs
    after the reply completes it in place, and the row is written once the
    pending tasks are done.
    """
    if fields.get("role") == "assistant":
        _captured_widget_signals.append(fields.get("answer_signals"))


def _apply_measurement_patches() -> None:
    """Turn the production pipeline into a measuring one: nothing it does may reach an employee's or visitor's data."""
    _make_database_read_only(database)
    replay.partner.record_internal_turn = replay._capture_internal_turn  # type: ignore[assignment]
    # A replayed follow-up neither reads the case the employee's own turn stored nor stores one.
    replay.partner.last_internal_case = replay._no_internal_case  # type: ignore[assignment]
    replay.partner.keep_internal_case = replay._skip_internal_case  # type: ignore[assignment]
    replay.partner.record_widget_turn = _capture_widget_turn  # type: ignore[assignment]
    # The retrieval log keys feedback to the chunks of the employee's latest turn,
    # and the gap event feeds the knowledge-gap backlog: neither may see a replay.
    replay.widget_turn.write_retrieval_log = replay._skip_retrieval_log  # type: ignore[assignment]
    retrieval_context._schedule_gap_event = _skip_gap_event  # type: ignore[assignment]
    answer_grounding._CHECK_TIMEOUT_SECONDS = _GROUNDING_BUDGET_SECONDS
    answer_grounding._INTERNAL_CHECK_TIMEOUT_SECONDS = _GROUNDING_BUDGET_SECONDS


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


async def _replay_turn(org: Any, thread: Thread, case: dict) -> dict:
    """One internal row. Index k is the position in the readable turns, u the position among the employee's turns."""
    user_turns = [k for k, turn in enumerate(thread.turns) if turn["role"] == "user"]
    u = case["turn"]
    if u >= len(user_turns):
        return {"cid": case["cid"], "u": u, "error": "turn_out_of_range"}
    k = user_turns[u]
    messages = [{"role": t["role"], "content": t["content"]} for t in thread.turns[: k + 1]]
    row = {"cid": case["cid"], "k": k, "u": u, "mode": thread.mode, "ask": messages[-1]["content"][:_ASK_CHARS]}
    return await _replayed(
        row,
        lambda: replay._new_answer(org, thread.profile, messages),  # type: ignore[arg-type]
        ("text", "signals", "total_ms"),
    )


def _read_widget(path: Path) -> dict:
    """The widget a round replays as: its id, org, knowledge bases, tenant slug and allowed origin."""
    try:
        widget = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read the widget file {path}: {exc}") from exc
    if not isinstance(widget, dict) or any(field not in widget for field in _WIDGET_FIELDS):
        raise UsageError(f"{path}: needs the fields {', '.join(_WIDGET_FIELDS)}")
    return widget


async def _widget_answer(widget: dict, messages: list[dict]) -> dict:
    """One widget turn through the route itself, as the browser sends it: a preview session token, the answer held.

    Held (not streamed) so the reply is complete when the response is; the
    signals are read after the tasks the route left behind (the record and
    the check after the reply) are done.
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
        session_id=uuid.uuid4().hex,
    )
    _captured_widget_signals.clear()
    started = time.perf_counter()
    # The ASGI transport runs the route in this process and honours no client
    # timeout, so the deadline is kept here; a turn that hangs is a restart.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://meetlat") as client:
        response = await asyncio.wait_for(
            client.post(
                "/partner/v1/chat/completions",
                json={"model": "klai-primary", "stream": False, "messages": messages},
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


async def _replay_widget_turn(widget: dict, case: dict) -> dict:
    """One widget row: the case's own conversation, replayed as the visitor's latest turn."""
    messages = [{"role": m["role"], "content": m["content"]} for m in case["messages"]]
    row = {"cid": case["cid"], "u": case["turn"], "ask": messages[-1]["content"][:_ASK_CHARS]}
    return await _replayed(
        row, lambda: _widget_answer(widget, messages), ("text", "sources", "appointment", "signals", "total_ms")
    )


def _rows_inside_checkout(out: Path) -> bool:
    """True when ``out`` lies inside a git checkout: rows hold real conversations and a checkout has a public remote.

    An overlay on the production host (``/out/<overlay>``) has no ``.git``
    and is not a repository, so rows may sit next to it.
    """
    root = replay.REPO_ROOT
    return (root / ".git").exists() and (out == root or root in out.parents)


async def _replayer(
    args: argparse.Namespace, widget: dict | None, todo: list[dict]
) -> Callable[[dict], Awaitable[dict]]:
    """How one case of this surface is replayed: a widget case as the widget, an internal case as its employee."""
    if widget is not None:
        return lambda case: _replay_widget_turn(widget, case)
    org = await replay._load_org(args.org)
    threads = await _fetch_threads(org, sorted({c["cid"] for c in todo}), args.days)

    async def replay_internal(case: dict) -> dict:
        thread = threads.get(case["cid"])
        if thread is None or thread.profile is None:
            return {"cid": case["cid"], "u": case["turn"], "error": "conversation_or_profile_not_found"}
        return await _replay_turn(org, thread, case)

    return replay_internal


async def _run(args: argparse.Namespace) -> int:
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
        return 0

    _apply_measurement_patches()
    replay._route_logs_to(out.with_name(out.name + ".pipeline.log"))
    replay_case = await _replayer(args, widget, todo)
    errors = 0
    with out.open("a") as handle:
        for number, case in enumerate(todo, 1):
            row = await replay_case(case)
            errors += "error" in row
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"{number}/{len(todo)} {'failed: ' + row['error'] if 'error' in row else 'replayed'}", flush=True)
    print(f"files: {out} ({errors} error row(s))")
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
            f"median total_ms {summary['median_total_ms']}; error or empty rows {summary['errors_or_empty']}"
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
    run = commands.add_parser("run", help="replay the cases through the chat pipeline")
    run.add_argument("--surface", choices=("internal", "widget"), required=True)
    run.add_argument("--org", required=True, help="org slug")
    run.add_argument("--cases", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument(
        "--overlay", type=Path, help="directory with the candidate code; every pipeline module must load from it"
    )
    run.add_argument("--days", type=int, default=_DEFAULT_DAYS, help="only conversations updated within this many days")
    run.add_argument(
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
        return asyncio.run(_run(args)) if args.command == "run" else _compare(args)
    except UsageError as exc:
        print(f"meetlat: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
