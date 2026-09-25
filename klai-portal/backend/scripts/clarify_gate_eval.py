"""Label and score the clarify gate (app/services/clarify_gate.py) on real questions.

SPEC-RAG-ANSWER-JUDGES-001 logbook 2.54: the decision to ask one question is
checked against human labels before full answers are judged, and every round
reruns only the gate over retrievals stored once.

Modes:
- ``sample <org_slug> [n]`` (portal-api container): up to n real first
  questions of the org's widget (first visitor message of each non-preview,
  non-test conversation of the last 60 days) and up to n LibreChat openings.
  Each question is retrieved once through retrieval-api with the body the live
  path sends (``retrieve_context``), without the paraphrase or rewrite model
  calls, and the evidence pack is cached in the output folder. The gate runs on
  every question and the question writer (klai-fast) only on the ones it
  fires on. Writes ``labels.jsonl`` for the owner to fill in.
- ``gate <sample folder>`` (local, no settings, no network, no model): the
  gate again over the questions and cached packs a ``sample`` run wrote, with
  the owner's labels carried over, so every change is re-scored for free.
- ``gate <replay.jsonl> [pool.json]`` (local, no settings, no network, no
  model): the gate over cached rows that carry ``top`` and ``titles`` only.
  Without per-chunk scores and heading paths every title counts as strong
  when ``top`` does, and only shared title words can link documents, so this
  mode over-reads the retrieval; ``pool.json`` supplies the conversations.
  Writes the same row shape as ``sample``.
- ``score <labels.jsonl> [cases.json]``: precision and recall of the gate
  against the owner's ``label_should_ask`` (y/n), the fire rate on weak
  retrievals and how often the gate's axis matches ``label_axis`` (one of
  device, direction, edition, product). With ``cases.json`` (reviewed
  conversations with a ``kind``: ask and options should ask, direct should
  not, either is left out) the same numbers for the rows whose question
  matches a case.

PRIVATE DATA: questions, titles and written questions go only to files in a
directory outside the repository (default /tmp/clarify-gate-<timestamp>/, or
KLAI_CLARIFY_OUT); stdout prints counts only. The owner labels labels.jsonl by
hand and it never enters the repository.

    docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 \\
        python scripts/clarify_gate_eval.py sample <org_slug> [n]
    python scripts/clarify_gate_eval.py gate <sample folder>
    python scripts/clarify_gate_eval.py gate <replay.jsonl> [pool.json]
    python scripts/clarify_gate_eval.py score <labels.jsonl> [cases.json]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.clarify_gate import ClarifyDecision, clarify_gate  # noqa: E402

REPO_ROOT = BACKEND_ROOT.parents[1]
_SAMPLE_DAYS = 60
_WIDGET_TOP_K = 8
# klai-fast allows 45 requests a minute per key; the writer stays well under it.
_WRITER_PAUSE_SECONDS = 1.5
# The gate's own threshold comes from settings in the service; the local modes
# have no settings, so they read the same environment variable and default.
_LOCAL_THRESHOLD = float(os.getenv("KLAI_GAP_SOFT_THRESHOLD", "0.4"))
_SHOULD_ASK = {"ask": True, "options": True, "direct": False}


def _out_dir() -> Path:
    default = Path(tempfile.gettempdir()) / f"clarify-gate-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out = Path(os.getenv("KLAI_CLARIFY_OUT") or default).resolve()
    if out == REPO_ROOT or REPO_ROOT in out.parents:
        raise SystemExit("KLAI_CLARIFY_OUT must be outside the repository: the rows are real questions.")
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    return out


def _row(
    row_id: str, surface: str, messages: list[dict], top: float | None, gate: ClarifyDecision, threshold: float
) -> dict[str, Any]:
    """One labelling row. ``fired`` is the gate's decision; the writer's outcome, if it ran, is in ``reason``."""
    return {
        "id": row_id,
        "surface": surface,
        "question": messages[-1]["content"] if messages else "",
        "context": messages[:-1],
        "top": top,
        "weak": top is None or top < threshold,
        "fired": gate.reason in ("asked", "model_failed", "question_shape"),
        "reason": gate.reason,
        "axis": gate.axis,
        "options": list(gate.options),
        "written_question": gate.question,
        "label_should_ask": "",
        "label_axis": "",
        "note": "",
    }


def _write_rows(out: Path, rows: list[dict]) -> Path:
    path = out / "labels.jsonl"
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def _rate(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "0/0"


def _print_gate_summary(rows: list[dict]) -> None:
    fired = [r for r in rows if r["fired"]]
    strong = [r for r in rows if not r["weak"]]
    weak = [r for r in rows if r["weak"]]
    print(f"questions {len(rows)}, gate fired {_rate(len(fired), len(rows))}")
    print(f"fired on strong retrievals {_rate(sum(r['fired'] for r in strong), len(strong))}")
    print(f"fired on weak retrievals {_rate(sum(r['fired'] for r in weak), len(weak))}")
    print("reasons " + ", ".join(f"{k} {v}" for k, v in Counter(r["reason"] for r in rows).most_common()))
    print("axes " + ", ".join(f"{k} {v}" for k, v in Counter(r["axis"] for r in fired).most_common()))


# --- gate: cached rows, local ----------------------------------------------------


def run_gate(replay_path: Path, pool_path: Path | None, threshold: float = _LOCAL_THRESHOLD) -> list[dict]:
    pool: dict[str, dict] = {}
    if pool_path is not None:
        pool = {entry["id"]: entry for entry in json.loads(pool_path.read_text())}
    rows = []
    for line in replay_path.read_text().splitlines():
        cached = json.loads(line)
        entry = pool.get(cached["id"])
        messages = [*entry["history"], {"role": "user", "content": entry["question"]}] if entry else []
        top = cached.get("top")
        chunks = [{"title": title, "reranker_score": top} for title in cached.get("titles") or []] if top else []
        rows.append(
            _row(
                cached["id"],
                cached.get("source", ""),
                messages,
                top,
                clarify_gate(messages, chunks, threshold),
                threshold,
            )
        )
    return rows


def run_gate_on_sample(folder: Path, threshold: float = _LOCAL_THRESHOLD) -> list[dict]:
    """The gate again over a folder ``sample`` wrote: its questions, its cached packs, its labels kept."""
    from klai_citations import evidence_pack_items_as_chunks

    rows = []
    for line in (folder / "labels.jsonl").read_text().splitlines():
        sampled = json.loads(line)
        chunks = evidence_pack_items_as_chunks(json.loads((folder / "packs" / f"{sampled['id']}.json").read_text()))
        messages = [*sampled["context"], {"role": "user", "content": sampled["question"]}]
        scores = [c["reranker_score"] for c in chunks if c.get("reranker_score") is not None]
        row = _row(
            sampled["id"],
            sampled["surface"],
            messages,
            max(scores, default=None),
            clarify_gate(messages, chunks, threshold),
            threshold,
        )
        rows.append({**row, **{key: sampled.get(key, "") for key in ("label_should_ask", "label_axis", "note")}})
    return rows


# --- score -----------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def score(rows: list[dict], truth: dict[str, bool]) -> dict[str, Any]:
    """Precision and recall of ``fired`` against ``truth`` (row id -> should ask)."""
    judged = [r for r in rows if r["id"] in truth]
    tp = sum(1 for r in judged if r["fired"] and truth[r["id"]])
    fp = sum(1 for r in judged if r["fired"] and not truth[r["id"]])
    fn = sum(1 for r in judged if not r["fired"] and truth[r["id"]])
    axis_rows = [r for r in judged if r["fired"] and r.get("label_axis")]
    weak = [r for r in rows if r["weak"]]
    return {
        "judged": len(judged),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "weak_fired": sum(1 for r in weak if r["fired"]),
        "weak": len(weak),
        "axis_matched": sum(1 for r in axis_rows if _norm(r["axis"] or "") == _norm(r["label_axis"])),
        "axis_labelled": len(axis_rows),
    }


def owner_truth(rows: list[dict]) -> dict[str, bool]:
    return {r["id"]: r["label_should_ask"].strip().lower() == "y" for r in rows if r["label_should_ask"].strip()}


def case_truth(rows: list[dict], cases: list[dict]) -> dict[str, bool]:
    """Rows whose question is a reviewed case's question; ``either`` cases are left out."""
    by_question = {_norm(c["question"]): _SHOULD_ASK[c["kind"]] for c in cases if c["kind"] in _SHOULD_ASK}
    return {r["id"]: by_question[_norm(r["question"])] for r in rows if _norm(r["question"]) in by_question}


def _print_score(label: str, result: dict[str, Any]) -> None:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    print(
        f"{label}: judged {result['judged']}, tp {result['true_positive']} fp {result['false_positive']} "
        f"fn {result['false_negative']}, precision {pct(result['precision'])}, recall {pct(result['recall'])}, "
        f"axis matched {result['axis_matched']}/{result['axis_labelled']}, "
        f"fired on weak retrievals {_rate(result['weak_fired'], result['weak'])}"
    )


# --- sample: real questions, in the portal-api container --------------------------


def _route_logs_to(path: Path) -> None:
    """The pipeline's own log lines go to a file in the output folder, never to stdout."""
    from app.logging_setup import setup_logging

    setup_logging("clarify-gate-eval")
    root = logging.getLogger()
    formatter = root.handlers[0].formatter
    root.handlers.clear()
    handler = logging.FileHandler(path)
    handler.setFormatter(formatter)
    root.addHandler(handler)


async def _widget_questions(org_id: int, count: int) -> list[tuple[str, str, list[str]]]:
    """(id, first visitor message, the widget's KB slugs), newest conversations first."""
    from sqlalchemy import func, select

    from app.core.database import tenant_scoped_session
    from app.models.knowledge_bases import PortalKnowledgeBase
    from app.models.widgets import WidgetConversation, WidgetKbAccess, WidgetMessage

    since = datetime.now(UTC) - timedelta(days=_SAMPLE_DAYS)
    first = (
        select(WidgetMessage.conversation_id, func.min(WidgetMessage.sequence).label("sequence"))
        .join(WidgetConversation, WidgetConversation.id == WidgetMessage.conversation_id)
        .where(
            WidgetConversation.org_id == org_id,
            WidgetConversation.is_preview.is_(False),
            WidgetConversation.is_test.is_(False),
            WidgetMessage.role == "user",
            WidgetMessage.created_at >= since,
        )
        .group_by(WidgetMessage.conversation_id)
        .subquery()
    )
    async with tenant_scoped_session(org_id) as db:
        messages = (
            await db.execute(
                select(WidgetMessage.id, WidgetMessage.content, WidgetConversation.widget_id)
                .join(
                    first,
                    (WidgetMessage.conversation_id == first.c.conversation_id)
                    & (WidgetMessage.sequence == first.c.sequence),
                )
                .join(WidgetConversation, WidgetConversation.id == WidgetMessage.conversation_id)
                .where(WidgetMessage.role == "user")
                .order_by(WidgetMessage.created_at.desc())
                .limit(count)
            )
        ).all()
        slugs: dict[str, list[str]] = {}
        for widget_id in {m.widget_id for m in messages}:
            slugs[widget_id] = list(
                (
                    await db.execute(
                        select(PortalKnowledgeBase.slug)
                        .join(WidgetKbAccess, WidgetKbAccess.kb_id == PortalKnowledgeBase.id)
                        .where(WidgetKbAccess.widget_id == widget_id, PortalKnowledgeBase.org_id == org_id)
                    )
                ).scalars()
            )
    return [(f"w-{m.id}", m.content, slugs[m.widget_id]) for m in messages]


async def _librechat_openings(org: Any, count: int) -> list[tuple[str, str, list[str] | None]]:
    """(id, the employee's first message, the KB slugs their profile searches or None for all)."""
    from app.core.config import settings
    from app.core.database import tenant_scoped_session
    from app.core.provisioning_names import provisioning_names_for_slug
    from app.services.chat_profile import resolve_internal_profile
    from app.services.internal_chat_identity import LibreChatIdentityError
    from app.services.librechat_quality_judge import _mongo_client, _sync_fetch_messages, _turns_from_messages

    database = provisioning_names_for_slug(org.slug, domain=settings.domain).mongodb_database
    since = datetime.now(UTC) - timedelta(days=_SAMPLE_DAYS)

    def read() -> list[dict]:
        with _mongo_client() as client:
            conversations = list(
                client[database]
                .conversations.find(
                    {"createdAt": {"$gte": since}, "user": {"$type": "string"}},
                    {"conversationId": 1, "user": 1, "_id": 0},
                )
                .sort("createdAt", -1)
                .limit(count * 3)
            )
        messages = _sync_fetch_messages(database, [c["conversationId"] for c in conversations])
        out = []
        for conversation in conversations:
            turns = _turns_from_messages(messages.get(conversation["conversationId"], []))
            opening = next((t["content"] for t in turns if t["role"] == "user"), None)
            if opening:
                out.append({"cid": conversation["conversationId"], "user": conversation["user"], "opening": opening})
        return out

    openings: list[tuple[str, str, list[str] | None]] = []
    async with tenant_scoped_session(org.id) as db:
        for candidate in await asyncio.to_thread(read):
            try:
                profile = await resolve_internal_profile(db, org, candidate["user"], remember=False)
            except LibreChatIdentityError:
                continue
            # General searches nothing, and a personal-only scope is no org retrieval.
            if profile.kb_mode == "general" or profile.kb_slugs == () or profile.kb_scope == "personal":
                continue
            kb_slugs = list(profile.kb_slugs) if profile.kb_slugs is not None else None
            openings.append((f"lc-{candidate['cid']}", candidate["opening"], kb_slugs))
            if len(openings) == count:
                break
    return openings


async def _evidence_pack(
    out: Path, row_id: str, question: str, zitadel_org_id: str, kb_slugs: list[str] | None, top_k: int
) -> dict:
    """The retrieval for one question, from the cache in the output folder or from retrieval-api once."""
    import httpx

    from app.core.config import settings

    cached = out / "packs" / f"{row_id}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    body: dict[str, Any] = {
        "query": question,
        "org_id": zitadel_org_id,
        "scope": "org",
        "top_k": top_k,
        "conversation_history": [],
        "telemetry_level": "off",
        # Not a tenant's question: no knowledge.queried event in its usage.
        "purpose": "background",
    }
    if kb_slugs:
        body["kb_slugs"] = kb_slugs
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.knowledge_retrieve_url}/retrieve",
            json=body,
            headers={
                "X-Internal-Secret": settings.retrieval_api_internal_secret or settings.internal_secret,
                "X-Caller-Service": "portal-api",
            },
        )
        response.raise_for_status()
    pack = response.json().get("evidence_pack") or {}
    cached.parent.mkdir(mode=0o700, exist_ok=True)
    cached.write_text(json.dumps(pack, ensure_ascii=False))
    return pack


async def run_sample(org_slug: str, count: int) -> None:
    from klai_citations import evidence_pack_items_as_chunks
    from sqlalchemy import select

    from app.core.config import settings
    from app.core.database import cross_org_session
    from app.models.portal import PortalOrg
    from app.services.clarify_decision import write_question
    from app.services.partner_chat import INTERNAL_RETRIEVE_TOP_K

    out = _out_dir()
    _route_logs_to(out / "pipeline.log")
    # The only cross-org read: the org id has to be known before a tenant scope can be set.
    async with cross_org_session() as session:
        org = (
            await session.execute(select(PortalOrg).where(PortalOrg.slug == org_slug, PortalOrg.deleted_at.is_(None)))
        ).scalar_one_or_none()
    if org is None:
        raise SystemExit("No active org with that slug.")
    threshold = settings.klai_gap_soft_threshold
    questions = [(*q, _WIDGET_TOP_K, "widget") for q in await _widget_questions(org.id, count)]
    questions += [(*q, INTERNAL_RETRIEVE_TOP_K, "librechat") for q in await _librechat_openings(org, count)]
    print(f"{len(questions)} questions sampled", flush=True)
    rows = []
    failed = 0
    for row_id, question, kb_slugs, top_k, surface in questions:
        try:
            pack = await _evidence_pack(out, row_id, question, org.zitadel_org_id, kb_slugs, top_k)
        except Exception:
            failed += 1
            logging.getLogger(__name__).exception("clarify_gate_eval_retrieval_failed")
            continue
        chunks = evidence_pack_items_as_chunks(pack)
        messages = [{"role": "user", "content": question}]
        scores = [c["reranker_score"] for c in chunks if c.get("reranker_score") is not None]
        gate = clarify_gate(messages, chunks, threshold)
        if gate.reason == "asked":
            gate = await write_question(messages, gate, settings, delegated_org_id=org.zitadel_org_id)
            await asyncio.sleep(_WRITER_PAUSE_SECONDS)
        rows.append(_row(row_id, surface, messages, max(scores, default=None), gate, threshold))
    path = _write_rows(out, rows)
    _print_gate_summary(rows)
    writer = Counter(r["reason"] for r in rows if r["fired"])
    print(
        f"question writer: asked {writer['asked']}, model_failed {writer['model_failed']}, "
        f"question_shape {writer['question_shape']}; retrieval failed {failed}"
    )
    print(f"files: {path.parent}")


def main(argv: list[str]) -> None:
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "sample" and len(argv) in (3, 4):
        asyncio.run(run_sample(argv[2], int(argv[3]) if len(argv) == 4 else 250))
    elif mode == "gate" and len(argv) in (3, 4):
        source = Path(argv[2])
        if source.is_dir():
            rows = run_gate_on_sample(source)
        else:
            rows = run_gate(source, Path(argv[3]) if len(argv) == 4 else None)
        path = _write_rows(_out_dir(), rows)
        _print_gate_summary(rows)
        print(f"conversations found for {sum(1 for r in rows if r['question'])}/{len(rows)} rows")
        print(f"files: {path.parent}")
    elif mode == "score" and len(argv) in (3, 4):
        rows = [json.loads(line) for line in Path(argv[2]).read_text().splitlines() if line.strip()]
        _print_score("owner labels", score(rows, owner_truth(rows)))
        if len(argv) == 4:
            _print_score("reviewed cases", score(rows, case_truth(rows, json.loads(Path(argv[3]).read_text()))))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
