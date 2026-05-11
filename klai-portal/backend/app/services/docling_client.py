"""Docling-serve async client.

SPEC-KB-FILE-UPLOAD-001 — wraps the three async endpoints exposed by
``docling-serve v1.16.1``:

- ``POST /v1/chunk/hybrid/file/async`` — submit a file, returns ``task_id``
  immediately. Conversion + chunking run in docling-serve's own worker queue.
- ``GET /v1/status/poll/{task_id}`` — current task status (``pending``,
  ``in_progress``, ``success``, ``failure``, etc.). Cheap to call.
- ``GET /v1/result/{task_id}`` — fetch the converted markdown once
  the task reports ``success``.

Why no Procrastinate layer in portal-api: docling-serve already runs an
async task queue. Wrapping it in a second queue would just shift state
around without adding robustness — portal-api persists the
``docling_task_id`` so any restart can resume polling against
docling-serve's authoritative task state.

The ``klai_image_storage`` library hosts the canonical pattern for
``asyncio.to_thread`` wrapping of sync SDKs; here we use httpx directly
because docling-serve is a plain HTTP service and httpx is already a
portal-api dep.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.trace import get_trace_headers

logger = structlog.get_logger()


# @MX:NOTE: docling-serve task statuses observed in production.
# @MX:REASON: Pinned here to keep the polling state machine total —
#   any unrecognised status surfaces in logs as ``unknown``.
class DoclingTaskStatus(StrEnum):
    """Status enum reported by ``docling-serve``'s ``task_status`` field."""

    PENDING = "pending"
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"


# Statuses we consider terminal — polling stops here.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {DoclingTaskStatus.SUCCESS, DoclingTaskStatus.FAILURE, DoclingTaskStatus.REVOKED}
)


@dataclass(frozen=True)
class DoclingSubmitResult:
    """Outcome of ``submit_file_async``."""

    task_id: str
    initial_status: str


@dataclass(frozen=True)
class DoclingPollResult:
    """Outcome of ``poll_status``. Terminal=True when polling should stop."""

    task_id: str
    status: str
    terminal: bool
    error_message: str | None
    queue_position: int | None


@dataclass(frozen=True)
class DoclingIngestResult:
    """Docling output normalized for knowledge-ingest."""

    content: str
    chunks: tuple[str, ...] | None
    chunk_count: int


class DoclingError(Exception):
    """Raised on any non-recoverable docling-serve failure."""


class DoclingTimeoutError(DoclingError):
    """Raised when docling-serve does not respond within the HTTP timeout."""


class DoclingResultNotFoundError(DoclingError):
    """Raised when docling-serve no longer has a completed task result."""


# Submission timeout: docling-serve buffers the upload to disk before
# returning the task_id. For a 200 MB file this can take 20-30 s on
# the existing pipe; 60 s gives plenty of headroom without holding the
# portal-api request hostage forever.
_SUBMIT_TIMEOUT_S: float = 60.0

# Status polls are cheap; 5 s is generous.
_STATUS_TIMEOUT_S: float = 5.0

# Result fetches return chunks and may include converted document
# metadata. 30 s covers slow links without hiding a stuck service.
_RESULT_TIMEOUT_S: float = 30.0

# knowledge-ingest IngestRequest.content is intentionally bounded. For
# pre-chunked Docling results, content is a representative preview while
# the complete text is sent in chunks[].
_CONTENT_PREVIEW_MAX_CHARS = 450_000
_FALLBACK_CHUNK_CHARS = 20_000

# Docling Serve defaults ``image_export_mode`` to ``embedded`` for Markdown,
# which can put base64 PNGs directly in md_content. A 129 MB textbook produced
# ~491 MB of markdown that exceeded knowledge-ingest's request schema. For RAG
# ingestion, image placeholders are enough; OCR text remains in the markdown.
_IMAGE_EXPORT_MODE = "placeholder"
_IMAGE_PLACEHOLDER = "<!-- image -->"
_EMBEDDED_DATA_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(data:image/[^)]*;base64,[^)]+\)")
_EXPLICIT_INPUT_FORMATS = frozenset(
    {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "html",
        "image",
        "csv",
        "md",
        "asciidoc",
        "json_docling",
        "xml_uspto",
        "xml_jats",
        "xml_xbrl",
        "mets_gbs",
        "audio",
        "vtt",
        "latex",
    }
)


def _client(timeout_s: float) -> httpx.AsyncClient:
    """Build an httpx client pointed at the configured docling-serve URL."""
    return httpx.AsyncClient(
        base_url=settings.docling_url,
        headers={**get_trace_headers()},
        timeout=timeout_s,
    )


async def submit_file_async(
    *,
    filename: str,
    content: bytes,
    content_type: str,
    input_format: str | None = None,
    to_formats: tuple[str, ...] = ("md",),
) -> DoclingSubmitResult:
    """Submit a file to docling-serve's async queue.

    The caller is responsible for ext+magic-byte validation BEFORE
    calling — this function trusts ``content_type`` and forwards as-is
    so docling-serve can route to the right parser.

    Returns the ``task_id`` to persist alongside the artifact for later
    polling. Raises :class:`DoclingError` on protocol failures and
    :class:`DoclingTimeoutError` on transport timeouts.
    """
    files = [("files", (filename, content, content_type))]
    # Use Docling Serve's own chunk endpoint for all binary office/document
    # formats. It preserves Docling's structural context while avoiding our
    # old "one giant markdown string" handoff to knowledge-ingest.
    data: dict[str, object] = {
        "include_converted_doc": False,
        "convert_image_export_mode": _IMAGE_EXPORT_MODE,
    }
    if input_format in _EXPLICIT_INPUT_FORMATS:
        data["convert_from_formats"] = [input_format]

    try:
        async with _client(_SUBMIT_TIMEOUT_S) as client:
            response = await client.post("/v1/chunk/hybrid/file/async", files=files, data=data)
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise DoclingTimeoutError(f"docling submit timed out for {filename!r}") from exc
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "docling_submit_http_error",
            filename=filename,
            status=exc.response.status_code,
        )
        raise DoclingError(
            f"docling submit returned {exc.response.status_code} for {filename!r}",
        ) from exc
    except httpx.RequestError as exc:
        logger.exception("docling_submit_request_error", filename=filename)
        raise DoclingError(f"docling submit transport error for {filename!r}") from exc

    task_id = payload.get("task_id")
    initial_status = payload.get("task_status", "pending")
    if not isinstance(task_id, str) or not task_id:
        raise DoclingError(
            f"docling submit returned malformed payload (no task_id) for {filename!r}",
        )

    logger.info(
        "docling_submit_accepted",
        filename=filename,
        task_id=task_id,
        initial_status=initial_status,
        bytes=len(content),
    )
    return DoclingSubmitResult(task_id=task_id, initial_status=initial_status)


async def poll_status(task_id: str) -> DoclingPollResult:
    """Poll a task's status. Cheap; safe to call frequently."""
    try:
        async with _client(_STATUS_TIMEOUT_S) as client:
            response = await client.get(f"/v1/status/poll/{task_id}")
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise DoclingTimeoutError(f"docling poll timed out for {task_id}") from exc
    except httpx.HTTPStatusError as exc:
        logger.exception("docling_poll_http_error", task_id=task_id, status=exc.response.status_code)
        raise DoclingError(f"docling poll returned {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.exception("docling_poll_request_error", task_id=task_id)
        raise DoclingError("docling poll transport error") from exc

    status = payload.get("task_status", "")
    return DoclingPollResult(
        task_id=task_id,
        status=status,
        terminal=status in _TERMINAL_STATUSES,
        error_message=payload.get("error_message"),
        queue_position=payload.get("task_position"),
    )


async def get_result_markdown(task_id: str) -> str:
    """Backward-compatible helper returning a markdown/text body."""
    result = await get_result_document(task_id)
    if result.chunks:
        return "\n\n".join(result.chunks)
    return result.content


async def get_result_document(task_id: str) -> DoclingIngestResult:
    """Fetch the converted markdown body for a successful task.

    The primary path expects Docling Serve's ``ChunkDocumentResponse``
    from ``/v1/chunk/hybrid/file/async`` and returns pre-computed chunks.
    A legacy ``ConvertDocumentResponse`` is still accepted defensively.

    Raises :class:`DoclingError` if the document body is missing or the
    conversion was marked as ``failed`` or ``skipped`` even though the
    task itself reached ``success``.
    """
    try:
        async with _client(_RESULT_TIMEOUT_S) as client:
            response = await client.get(f"/v1/result/{task_id}")
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise DoclingTimeoutError(f"docling result fetch timed out for {task_id}") from exc
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "docling_result_http_error",
            task_id=task_id,
            status=exc.response.status_code,
        )
        if exc.response.status_code == 404:
            raise DoclingResultNotFoundError(f"docling result not found for {task_id}") from exc
        raise DoclingError(f"docling result returned {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        logger.exception("docling_result_request_error", task_id=task_id)
        raise DoclingError("docling result transport error") from exc

    return _extract_ingest_result(payload, task_id)


def _strip_embedded_images(markdown: str, task_id: str) -> str:
    """Replace embedded base64 image markdown with a stable placeholder."""
    stripped, count = _EMBEDDED_DATA_IMAGE_RE.subn(_IMAGE_PLACEHOLDER, markdown)
    if count:
        logger.info(
            "docling_markdown_embedded_images_stripped",
            task_id=task_id,
            images=count,
            original_chars=len(markdown),
            stripped_chars=len(stripped),
        )
    return stripped


def _content_preview(texts: tuple[str, ...]) -> str:
    """Return a bounded representative body for metadata/classification."""
    parts: list[str] = []
    remaining = _CONTENT_PREVIEW_MAX_CHARS
    for text in texts:
        if remaining <= 0:
            break
        if not text:
            continue
        part = text[:remaining]
        parts.append(part)
        remaining -= len(part) + 2
    return "\n\n".join(parts).strip()


def _fallback_chunks(markdown: str) -> tuple[str, ...]:
    """Chunk legacy converted markdown if Docling did not return chunks."""
    chunks = tuple(
        markdown[i : i + _FALLBACK_CHUNK_CHARS].strip()
        for i in range(0, len(markdown), _FALLBACK_CHUNK_CHARS)
        if markdown[i : i + _FALLBACK_CHUNK_CHARS].strip()
    )
    return chunks or (markdown,)


def _extract_ingest_result(payload: Any, task_id: str) -> DoclingIngestResult:
    """Normalize Docling chunk or conversion responses for ingestion."""
    if isinstance(payload, dict) and isinstance(payload.get("chunks"), list):
        raw_chunks = payload["chunks"]
        texts: list[str] = []
        for item in raw_chunks:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(_strip_embedded_images(text, task_id).strip())
        chunks = tuple(texts)
        if not chunks:
            raise DoclingError(f"docling produced no chunks for {task_id}")
        return DoclingIngestResult(
            content=_content_preview(chunks),
            chunks=chunks,
            chunk_count=len(chunks),
        )

    markdown = _extract_markdown(payload, task_id)
    chunks = _fallback_chunks(markdown) if len(markdown) > _CONTENT_PREVIEW_MAX_CHARS else None
    return DoclingIngestResult(
        content=_content_preview(chunks) if chunks else markdown,
        chunks=chunks,
        chunk_count=len(chunks) if chunks else 0,
    )


def _extract_markdown(payload: Any, task_id: str) -> str:
    """Pull the ``md_content`` field from a single-document response.

    Defensive: docling-serve may return ``status: "skipped"`` with no
    body when a file was rejected (unsupported format, corrupt). We
    treat any non-``success`` conversion status as a hard failure.
    """
    document = payload.get("document") if isinstance(payload, dict) else None
    if not isinstance(document, dict):
        raise DoclingError(f"docling result missing 'document' for {task_id}")

    md = document.get("md_content")
    if not isinstance(md, str) or not md.strip():
        status = payload.get("status")
        errors = payload.get("errors") or []
        raise DoclingError(
            f"docling produced no markdown for {task_id} (status={status}, errors={errors!r})",
        )
    return _strip_embedded_images(md, task_id)
