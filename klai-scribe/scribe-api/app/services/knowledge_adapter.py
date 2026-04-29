"""
Scribe knowledge adapter.
Ingests a scribe transcription into the klai knowledge pipeline
by calling knowledge-ingest POST /ingest/v1/document.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_SEGMENTS_PER_CHUNK = 4  # target 3-5 segments per chunk
_MAX_TOKENS_PER_CHUNK = 400


async def ingest_scribe_transcript(
    org_id: str,
    kb_slug: str,
    transcription,  # Transcription SQLAlchemy model instance
) -> str:
    """
    Ingest a Scribe transcription into the knowledge pipeline.
    Returns the artifact_id from knowledge-ingest.
    """
    content_type = _detect_content_type(transcription)
    chunks = _chunk_transcription(transcription)
    full_text = transcription.text or ""

    payload = {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "path": f"scribe/{transcription.id}",
        "content": full_text,
        "title": transcription.name or "Untitled recording",
        "source_type": "connector",
        "content_type": content_type,
        "skip_chunking": True,
        "chunks": chunks,
        "synthesis_depth": 0,
        "extra": {
            "recording_duration_seconds": float(transcription.duration_seconds) if transcription.duration_seconds else None,
            "scribe_id": transcription.id,
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # SPEC-SEC-INTERNAL-001 REQ-9.4: header is unconditional. The
        # Settings validator on knowledge_ingest_secret enforces non-empty
        # at startup; the previous ``if settings.knowledge_ingest_secret:``
        # silent-omit guard would have allowed unauthenticated traffic
        # whenever the env var was missing.
        headers = {"X-Internal-Secret": settings.knowledge_ingest_secret}
        resp = await client.post(
            f"{settings.knowledge_ingest_url}/ingest/v1/document",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("artifact_id", "")


def _detect_content_type(transcription) -> str:
    """Use recording_type as authoritative signal."""
    mapping = {"meeting": "meeting_transcript", "recording": "1on1_transcript"}
    return mapping.get(getattr(transcription, "recording_type", None) or "", "meeting_transcript")


def _chunk_transcription(transcription) -> list[str]:
    """
    Chunk transcription by whisper segment clusters (3-5 segments per chunk).
    Falls back to paragraph splitting when segments_json is absent.
    """
    segments_json = getattr(transcription, "segments_json", None)
    if segments_json:
        return _cluster_segments(segments_json)
    return _split_paragraphs(transcription.text or "")


def _cluster_segments(segments: list[dict]) -> list[str]:
    """Group consecutive Whisper segments into clusters of ~4 segments per chunk."""
    if not segments:
        return []
    chunks = []
    current: list[str] = []
    current_chars = 0
    max_chars = _MAX_TOKENS_PER_CHUNK * _CHARS_PER_TOKEN

    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        if len(current) >= _SEGMENTS_PER_CHUNK or (current_chars + len(text) > max_chars and current):
            chunks.append(" ".join(current))
            current = []
            current_chars = 0
        current.append(text)
        current_chars += len(text)

    if current:
        chunks.append(" ".join(current))
    return chunks


def _split_paragraphs(text: str) -> list[str]:
    """Split by double newline, return non-empty paragraphs."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]
