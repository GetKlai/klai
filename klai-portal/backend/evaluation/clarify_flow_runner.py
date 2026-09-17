"""REQ-0 baseline runner for SPEC-RAG-CLARIFY-FLOW-001, path B only.

Measures how the public widget (pad B) handles vague, answerable, and
not-in-knowledge-base questions TODAY, before REQ-2/REQ-3 change its
behaviour. Drives the real production endpoints a visitor's browser would
call - GET /partner/v1/widget-config then POST /partner/v1/chat/completions
with the returned session token - never /retrieve or any internal route.

Usage:
    cd klai-portal/backend
    uv run python evaluation/clarify_flow_runner.py

See evaluation/README.md for the full command, defaults, and the "Test
traffic" section explaining why this run cannot mark its own conversations
as widget_conversations.is_test.

This script CANNOT run in standard CI: it needs network access to
api.getklai.com and writes real (uncounted-as-test) rows on Klai's own
tenant. It is designed for manual, occasional invocation - see the
production bugfix gate in AGENTS.md, REQ-0.

The SSE parser and the outcome classifier are pure functions with no I/O,
unit-tested with fixed input in ../tests/test_clarify_flow_runner.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from klai_chat_prompts import no_citable_sources_message

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_QUESTIONS_FILE = EVAL_DIR / "clarify_flow_questions.yaml"
DEFAULT_OUTPUT_DIR = EVAL_DIR / "results"
DEFAULT_BASE_URL = "https://api.getklai.com"
DEFAULT_WIDGET_ID = "wgt_47b8c9c46d10b17c527923e0a5454bef3285b71f"
DEFAULT_ORIGIN = "https://getklai.com"
DEFAULT_DELAY_SECONDS = 2.0
DEFAULT_SAMPLES = 1
_REQUEST_TIMEOUT = 45.0
_VALID_CATEGORIES = {"vague", "answerable", "not_in_kb"}

# The exact canned refusal, both languages, both helpdesk variants (spec.md
# "Hoe het nu werkt": partner_chat.py's `helpdesk` param follows the
# widget's own `support_mode` setting, so either variant can appear
# depending on which widget is targeted).
CANNED_REFUSALS = frozenset(
    no_citable_sources_message(lang, helpdesk=helpdesk) for lang in ("nl", "en") for helpdesk in (False, True)
)


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    language: str
    text: str


def load_questions(path: Path) -> list[Question]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = [Question(**item) for item in data["questions"]]
    for q in questions:
        if q.category not in _VALID_CATEGORIES:
            raise ValueError(f"{q.id}: unknown category {q.category!r}")
    return questions


def parse_sse_stream(raw_lines: list[str]) -> dict[str, Any]:
    """Parse the OpenAI-style SSE frames ``/partner/v1/chat/completions`` emits.

    Every frame is ``data: {"choices": [{"delta": {...}}]}`` with no
    ``event:`` line (see ``_sse_content_delta`` and its siblings in
    ``app/services/partner_chat.py``), terminated by ``data: [DONE]``.
    Concatenates every ``delta.content`` chunk; keeps the last value seen
    for the single-shot fields, matching how the widget's own client
    accumulates them over the stream.
    """
    content_parts: list[str] = []
    sources: list[dict] | None = None
    language: str | None = None
    broad_mode: str | None = None
    escalation: dict | None = None
    for line in raw_lines:
        if not line or not line.startswith("data: "):
            continue
        payload = line[len("data: ") :].strip()
        if payload == "[DONE]":
            break
        event = json.loads(payload)
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if isinstance(delta.get("content"), str):
            content_parts.append(delta["content"])
        if "sources" in delta:
            sources = delta["sources"]
        if "language" in delta:
            language = delta["language"]
        if "broad_mode" in delta:
            broad_mode = delta["broad_mode"]
        if "escalation" in delta:
            escalation = delta["escalation"]
    return {
        "content": "".join(content_parts),
        "sources": sources,
        "language": language,
        "broad_mode": broad_mode,
        "escalation": escalation,
    }


def classify_turn(content: str, sources: list[dict] | None) -> str:
    """Bucket one assistant turn per spec.md REQ-0: refusal / question / answer.

    ``clarifying_question`` is a deliberate simplification: no sources, not
    the canned refusal, and the text ends in a question mark. REQ-1a caps a
    wedervraag at one short question, so this heuristic had no false
    negative across the live spot-checks done for this baseline (see
    README "Live-checked questions"). A model-judged classifier is REQ-1b's
    job, not this measurement's - spec.md is explicit that REQ-0 must not
    reuse it.
    """
    stripped = content.strip()
    if stripped in CANNED_REFUSALS:
        return "canned_refusal"
    if sources:
        return "answer_with_sources"
    if stripped.endswith("?"):
        return "clarifying_question"
    return "answer_without_sources"


async def fetch_session_token(http: httpx.AsyncClient, *, base_url: str, widget_id: str, origin: str) -> str:
    resp = await http.get(
        f"{base_url}/partner/v1/widget-config",
        params={"id": widget_id},
        headers={"Origin": origin},
    )
    resp.raise_for_status()
    return resp.json()["session_token"]


async def ask_one(
    http: httpx.AsyncClient,
    question: Question,
    sample: int,
    *,
    base_url: str,
    origin: str,
    session_token: str,
) -> dict[str, Any]:
    turn_id = secrets.token_hex(16)  # 32 hex chars, within ^[0-9a-f]{16,64}$
    row: dict[str, Any] = {
        "question_id": question.id,
        "category": question.category,
        "language": question.language,
        "text": question.text,
        "sample": sample,
        "widget_turn_id": turn_id,
        "error": None,
    }
    try:
        async with http.stream(
            "POST",
            f"{base_url}/partner/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {session_token}",
                "Origin": origin,
                "Content-Type": "application/json",
            },
            json={
                "messages": [{"role": "user", "content": question.text}],
                "stream": True,
                "widget_turn_id": turn_id,
            },
            timeout=_REQUEST_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            lines = [line async for line in resp.aiter_lines()]
    except httpx.HTTPError as exc:
        row["error"] = str(exc)
        row["outcome"] = "request_failed"
        return row

    parsed = parse_sse_stream(lines)
    row.update(parsed)
    row["outcome"] = classify_turn(parsed["content"], parsed["sources"])
    return row


async def run(
    questions: list[Question],
    *,
    base_url: str,
    widget_id: str,
    origin: str,
    samples: int,
    delay_seconds: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as http:
        session_token = await fetch_session_token(http, base_url=base_url, widget_id=widget_id, origin=origin)
        first = True
        for question in questions:
            for sample in range(samples):
                if not first:
                    await asyncio.sleep(delay_seconds)
                first = False
                row = await ask_one(
                    http, question, sample, base_url=base_url, origin=origin, session_token=session_token
                )
                logger.info(
                    "clarify_flow_turn id=%s category=%s outcome=%s",
                    question.id,
                    question.category,
                    row["outcome"],
                )
                results.append(row)
    return results


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_summary(rows: list[dict[str, Any]]) -> str:
    """Markdown: counts and percentages per category x outcome, plus the
    manual-review list REQ-0 requires for every answer_without_sources and
    clarifying_question turn (spec.md line 62). The "org claim?" column is
    left blank here for a human to fill by hand - REQ-0 explicitly forbids
    scoring it with REQ-1b's own classifier.
    """
    categories = ["vague", "answerable", "not_in_kb"]
    outcomes = [
        "canned_refusal",
        "clarifying_question",
        "answer_with_sources",
        "answer_without_sources",
        "request_failed",
    ]
    counts: dict[str, Counter[str]] = {cat: Counter() for cat in categories}
    for row in rows:
        counts[row["category"]][row["outcome"]] += 1

    lines = ["# REQ-0 baseline - path B (widget)", ""]
    lines.append("| category | total | " + " | ".join(outcomes) + " |")
    lines.append("|---|---|" + "---|" * len(outcomes))
    for cat in categories:
        total = sum(counts[cat].values())
        cells = []
        for outcome in outcomes:
            n = counts[cat][outcome]
            pct = f"{n / total:.0%}" if total else "n/a"
            cells.append(f"{n} ({pct})")
        lines.append(f"| {cat} | {total} | " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## Manual review: answer_without_sources / clarifying_question")
    lines.append("")
    lines.append("Every doorgelaten text without a source, judged by hand for a claim about")
    lines.append("the organisation (product, price, procedure, setting, availability).")
    lines.append("")
    lines.append("| id | category | outcome | text | org claim? |")
    lines.append("|---|---|---|---|---|")
    for row in rows:
        if row["outcome"] in ("answer_without_sources", "clarifying_question"):
            text = row["content"].replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {row['question_id']} | {row['category']} | {row['outcome']} | {text} |  |")

    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--widget-id", default=DEFAULT_WIDGET_ID)
    parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="Repeats per question")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Seconds between requests")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    questions = load_questions(args.questions)
    logger.info("Loaded %d questions from %s", len(questions), args.questions)

    rows = asyncio.run(
        run(
            questions,
            base_url=args.base_url,
            widget_id=args.widget_id,
            origin=args.origin,
            samples=args.samples,
            delay_seconds=args.delay,
        )
    )

    write_jsonl(rows, args.output_dir / "clarify_flow_baseline.jsonl")
    summary = build_summary(rows)
    (args.output_dir / "clarify_flow_baseline_summary.md").write_text(summary, encoding="utf-8")
    print(summary)

    failed = sum(1 for row in rows if row["outcome"] == "request_failed")
    if failed == len(rows):
        logger.error("All %d requests failed - see errors in the JSONL", failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
