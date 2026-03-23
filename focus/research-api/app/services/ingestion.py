"""
Document ingestion pipeline — runs as FastAPI BackgroundTask.

Flow: route source -> docling/youtube/text -> chunk -> embed -> pgvector INSERT
"""
import logging
import uuid
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.source import Source
from app.services import chunker, docling, tei, youtube

logger = logging.getLogger(__name__)

_UPLOAD_BASE = Path("/opt/klai/research-uploads")


async def ingest_source(source_id: str) -> None:
    """
    Entry point for BackgroundTask. Opens its own DB session (request session is closed).
    """
    async with AsyncSessionLocal() as db:
        await _run_ingestion(db, source_id)


async def _run_ingestion(db: AsyncSession, source_id: str) -> None:
    from sqlalchemy import select

    row = await db.execute(select(Source).where(Source.id == source_id))
    source: Source | None = row.scalar_one_or_none()
    if source is None:
        logger.error("Ingestion: source %s not found", source_id)
        return

    await _set_status(db, source_id, "processing")

    try:
        text = await _extract_text(source)
        chunks = chunker.chunk_text(
            text=text,
            source_id=source_id,
            notebook_id=source.notebook_id,
            tenant_id=str(source.tenant_id),
        )
        if not chunks:
            raise ValueError("Geen tekst gevonden in het document")

        texts = [c["content"] for c in chunks]
        embeddings = await tei.embed_texts(texts)

        await _store_chunks(db, chunks, embeddings)

        await _set_status(db, source_id, "ready", chunks_count=len(chunks))
        logger.info("Ingestion complete for source %s: %d chunks", source_id, len(chunks))

    except Exception as exc:
        logger.exception("Ingestion failed for source %s", source_id)
        await _set_status(db, source_id, "error", error_message=str(exc))


async def _extract_text(source: Source) -> str:
    """Route by source type to extract plain text."""
    src_type = source.type

    if src_type == "text":
        file_path = Path(source.file_path)
        return file_path.read_text(encoding="utf-8")

    if src_type == "youtube":
        return youtube.get_transcript(source.original_ref)

    if src_type == "url":
        result = await docling.convert_url(source.original_ref)
        return result.text

    # File types: pdf, docx, xlsx, pptx
    file_path = Path(source.file_path)
    result = await docling.convert_file(file_path.read_bytes(), file_path.name)
    return result.text


async def _store_chunks(
    db: AsyncSession,
    chunks: list[dict],
    embeddings: list[list[float]],
) -> None:
    from app.models.chunk import Chunk

    for chunk_data, embedding in zip(chunks, embeddings):
        chunk = Chunk(
            id="chk_" + uuid.uuid4().hex[:24],
            source_id=chunk_data["source_id"],
            notebook_id=chunk_data["notebook_id"],
            tenant_id=chunk_data["tenant_id"],
            content=chunk_data["content"],
            metadata_=chunk_data.get("metadata"),
            embedding=embedding,
        )
        db.add(chunk)

    await db.commit()


async def _set_status(
    db: AsyncSession,
    source_id: str,
    status: str,
    chunks_count: int | None = None,
    error_message: str | None = None,
) -> None:
    values: dict = {"status": status}
    if chunks_count is not None:
        values["chunks_count"] = chunks_count
    if error_message is not None:
        values["error_message"] = error_message

    await db.execute(update(Source).where(Source.id == source_id).values(**values))
    await db.commit()
