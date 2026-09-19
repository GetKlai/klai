"""Controlled source-derived probe for the support gap assessor."""

import asyncio
import hashlib
import json
from collections import Counter

from app.services import support_case_analysis as sca

_ESTABLISHED = frozenset({"covered", "findability"})
_SCORED = frozenset({"detected_missing", "withheld_claimed_present"})


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _cases(snapshot: object, limit: int) -> tuple[tuple[str, str], list[tuple[str, dict]]]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("snapshot_id"), str):
        raise TypeError("snapshot_id must be a string")
    chunks = snapshot.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("chunks must be a nonempty list")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    scope: tuple[str, str] | None = None
    cases: list[tuple[str, dict]] = []
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise TypeError(f"chunk {index} must be an object")
        required = ("chunk_id", "org_id", "kb_slug", "text")
        if any(not isinstance(chunk.get(key), str) or not chunk[key] for key in required):
            raise ValueError(f"chunk {index} has an invalid required string")
        questions = chunk.get("questions")
        if not isinstance(questions, list) or any(not isinstance(q, str) or not q.strip() for q in questions):
            raise ValueError(f"chunk {index} has invalid questions")
        chunk_scope = (chunk["org_id"], chunk["kb_slug"])
        scope = scope or chunk_scope
        if chunk_scope != scope:
            raise ValueError("snapshot contains mixed org_id or kb_slug scopes")
        source = {key: chunk[key] for key in ("chunk_id", "kb_slug", "text")}
        source.update({key: chunk[key] for key in ("artifact_id",) if isinstance(chunk.get(key), str)})
        for question in questions:
            if len(cases) == limit:
                break
            cases.append((question.strip(), source))
    if not cases:
        raise ValueError("snapshot contains no questions")
    assert scope is not None
    return scope, cases


async def _trial(question_text: str, source: dict, scope: tuple[str, str], *, withheld: bool) -> dict:
    message_id = f"ingest-{hashlib.sha256(question_text.encode()).hexdigest()[:16]}"
    question = sca._Question(question_text, "unknown", "customer", "", [message_id])
    messages = {message_id: {"id": message_id, "role": "customer", "text": question_text}}

    async def retrieve(*_args, **_kwargs) -> list[dict]:
        return [] if withheld else [source]

    return await sca._analyze_question(
        question,
        messages,
        kb_slug=scope[1],
        zitadel_org_id=scope[0],
        user_id=None,
        retriever=retrieve,
    )


async def evaluate_ingest_snapshot(snapshot: dict, *, limit: int = 10, budget_seconds: float | None = None) -> dict:
    """Assess source-present/withheld states and return a text-free report."""
    scope, cases = _cases(snapshot, limit)
    deadline = asyncio.get_running_loop().time() + budget_seconds if budget_seconds is not None else None
    results: list[dict] = []
    for question, source in cases:
        result = {
            "question_id": hashlib.sha256(question.encode()).hexdigest(),
            "source_chunk_id": source["chunk_id"],
            "source_artifact_id": source.get("artifact_id"),
            "source_content_hash": hashlib.sha256(source["text"].encode()).hexdigest(),
        }
        try:
            async with asyncio.timeout_at(deadline):
                present = await _trial(question, source, scope, withheld=False)
                result["present_diagnosis"] = present["diagnosis"]
                if present["diagnosis"] not in _ESTABLISHED:
                    result["status"] = "unscorable"
                else:
                    withheld = await _trial(question, source, scope, withheld=True)
                    diagnosis = withheld["diagnosis"]
                    result["withheld_diagnosis"] = diagnosis
                    if diagnosis == "missing":
                        result["status"] = "detected_missing"
                    elif diagnosis in _ESTABLISHED:
                        result["status"] = "withheld_claimed_present"
                    else:
                        result["status"] = "unscorable"
        except Exception as exc:
            result.update(status="failed", error_type=type(exc).__name__)
            http_status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(http_status, int):
                result["http_status"] = http_status
        results.append(result)

    counts = Counter(result["status"] for result in results)
    scored = sum(counts[status] for status in _SCORED)
    if counts["failed"] or counts["withheld_claimed_present"]:
        quality_status = "failed"
    elif counts["unscorable"] or not scored:
        quality_status = "inconclusive"
    else:
        quality_status = "passed"
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_hash": _digest(snapshot),
        "scope_hash": _digest({"org_id": scope[0], "kb_slug": scope[1]}),
        "analyzer_version": sca.ANALYSIS_VERSION,
        "judge_model": sca.settings.conversation_judge_model,
        "measurement": "controlled_detector_assessment_only_not_retrieval_recall_or_independent_accuracy",
        "quality_status": quality_status,
        "counts": dict(sorted(counts.items())),
        "scored": scored,
        "unscorable": counts["unscorable"],
        "failed": counts["failed"],
        "results": results,
    }


def report_exit_code(report: dict) -> int:
    return int(report.get("quality_status") != "passed")
