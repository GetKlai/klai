"""
Knowledge adapter for Vexa meetings.
Ingests a VexaMeeting transcript into the klai knowledge pipeline
by calling knowledge-ingest POST /ingest/v1/document.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_TURNS_PER_CHUNK = 4  # target 3-5 speaker turns per chunk
_MAX_TOKENS_PER_CHUNK = 400


async def ingest_vexa_meeting(
    org_id: str,
    kb_slug: str,
    meeting,  # VexaMeeting SQLAlchemy model instance
) -> str:
    """
    Ingest a VexaMeeting transcript into the knowledge pipeline.
    Returns the artifact_id from knowledge-ingest.

    content_type is always meeting_transcript -- meetings are always multi-party.
    Speaker labels are available from Vexa's diarized transcript_segments.
    """
    segments = meeting.transcript_segments or []
    chunks = _chunk_by_speaker_turns(segments)
    full_text = " ".join(seg.get("text", "") for seg in segments).strip()
    participants = _extract_participants(segments)

    payload = {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "path": f"meeting/{meeting.id}",
        "content": full_text,
        "title": meeting.meeting_title or "Untitled meeting",
        "source_type": "connector",
        "content_type": "meeting_transcript",
        "skip_chunking": True,
        "chunks": chunks,
        "synthesis_depth": 0,
        "extra": {
            "participants": participants,
            "platform": meeting.platform,
            "meeting_title": meeting.meeting_title,
            "meeting_id": str(meeting.id),
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {}
        if settings.knowledge_ingest_secret:
            headers["X-Internal-Secret"] = settings.knowledge_ingest_secret
        resp = await client.post(
            f"{settings.knowledge_ingest_url}/ingest/v1/document",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("artifact_id", "")


def _chunk_by_speaker_turns(segments: list[dict]) -> list[str]:
    """
    Group consecutive speaker turns into clusters of ~4 turns per chunk.
    Respects max token budget as soft ceiling.
    Each chunk preserves speaker attribution: "Speaker: text"
    """
    if not segments:
        return []

    chunks = []
    current: list[str] = []
    current_chars = 0
    max_chars = _MAX_TOKENS_PER_CHUNK * _CHARS_PER_TOKEN

    for seg in segments:
        speaker = seg.get("speaker", "").strip()
        text = seg.get("text", "").strip()
        if not text:
            continue

        line = f"{speaker}: {text}" if speaker else text

        if len(current) >= _TURNS_PER_CHUNK or (current_chars + len(line) > max_chars and current):
            chunks.append("\n".join(current))
            current = []
            current_chars = 0

        current.append(line)
        current_chars += len(line)

    if current:
        chunks.append("\n".join(current))

    return chunks


def _extract_participants(segments: list[dict]) -> list[dict]:
    """Extract unique speaker names from transcript segments."""
    seen: set[str] = set()
    participants: list[dict] = []
    for seg in segments:
        speaker = seg.get("speaker", "").strip()
        if speaker and speaker not in seen:
            seen.add(speaker)
            participants.append({"name": speaker, "role": ""})
    return participants
