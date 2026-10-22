"""Chat-attachment normalisation: convert an active PDF file part in the
latest user turn into plain text before retrieval and generation.

Moved from ``deploy/litellm/klai_chat_attachments.py`` (one-chat-pipeline
plan, slice 3): this is input normalisation at the entry of the knowledge
path, identical for every chat surface (widget, partner, and eventually
internal), so it belongs in portal rather than behind a LiteLLM-only hook.
Reuses the portal docling client (``app/services/docling_client.py``) —
its polling loop already handles docling-serve's real four-value
``task_status`` vocabulary (pending/started/success/failure; see that
module's docstring) instead of the hook's now-redundant custom poll loop.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import time
from dataclasses import dataclass, field
from typing import Any

from klai_chat_prompts import _language_is_dutch as language_is_dutch

from app.core.config import settings
from app.services import docling_client
from app.services.docling_client import DoclingError, DoclingTaskStatus, DoclingTimeoutError

_IMAGE_PLACEHOLDER = "<!-- image -->"
_EMBEDDED_DATA_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(data:image/[^)]*;base64,[^)]+\)")

# Marks a converted PDF in the latest user message's text content. Exported so
# app.services.user_provided_content can recognise a converted PDF as an
# attachment without duplicating the literal.
UPLOADED_PDF_CONTENT_MARKER = "[Uploaded PDF content]"


@dataclass(frozen=True)
class ChatAttachmentResult:
    messages: list[dict[str, Any]]
    processed_count: int
    meta: dict[str, Any]
    user_visible_error: str | None = None
    # Filenames of the file parts that are not PDFs. They are already removed
    # from ``messages``; whether that is worth a notice or a refusal is the
    # caller's call.
    unreadable: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Attachment:
    message_index: int
    filename: str
    content: bytes
    user_text: str


class ChatAttachmentError(Exception):
    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _default_meta() -> dict[str, Any]:
    return {
        "chat_pdf_attachments_seen": 0,
        "chat_pdf_attachments_processed": 0,
        "chat_pdf_bytes": 0,
        "chat_pdf_extracted_chars": 0,
        "chat_pdf_extracted_tokens_estimate": 0,
        "chat_pdf_processing_ms": 0,
        "chat_pdf_error_reason": None,
    }


def user_visible_error(reason: str, language: object) -> str:
    """Deterministic attachment-failure text for the user.

    Takes the conversation language CODE, never raw query text — the same
    contract as the widget's refusal strings. Dutch for ``"nl"`` and for no
    decision (None, empty, ``"und"``); English for any other explicit code.
    """
    if language_is_dutch(language):
        if reason == "file_too_large":
            return (
                "Deze PDF is te groot om direct in chat te verwerken. Zet het "
                "document in de kennisbank of upload een kleiner bestand."
            )
        if reason == "extracted_text_too_large":
            return (
                "Deze PDF bevat te veel tekst om direct in chat te verwerken. "
                "Zet het document in de kennisbank of gebruik een kleiner document."
            )
        if reason == "unreadable_pdf":
            return "Deze PDF bevat geen leesbare tekst die Klai direct kan gebruiken."
        if reason == "processing_timeout":
            return (
                "Het verwerken van deze PDF duurt te lang voor chat. Zet het "
                "document in de kennisbank of probeer een kleiner bestand."
            )
        if reason == "too_many_attachments":
            return "Upload maximaal één PDF tegelijk in chat."
        if reason == "unsupported_attachment":
            return "Klai kan nu alleen PDF-bestanden direct in chat verwerken."
        return "Deze PDF kan niet direct in chat worden verwerkt."

    if reason == "file_too_large":
        return (
            "This PDF is too large to process directly in chat. Add it to the knowledge base or upload a smaller file."
        )
    if reason == "extracted_text_too_large":
        return (
            "This PDF contains too much text to process directly in chat. Add it "
            "to the knowledge base or use a smaller document."
        )
    if reason == "unreadable_pdf":
        return "This PDF does not contain readable text Klai can use directly."
    if reason == "processing_timeout":
        return "Processing this PDF is taking too long for chat. Add it to the knowledge base or try a smaller file."
    if reason == "too_many_attachments":
        return "Upload one PDF at a time in chat."
    if reason == "unsupported_attachment":
        return "Klai can currently process only PDF files directly in chat."
    return "This PDF cannot be processed directly in chat."


def _latest_user_message_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], dict) and messages[index].get("role") == "user":
            return index
    return None


def _text_from_parts(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts: list[str] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            text = part["text"].strip()
            if text:
                texts.append(text)
    return "\n\n".join(texts)


def _extract_data_url_bytes(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise ChatAttachmentError("invalid_file_payload")
    marker = ";base64,"
    if value.startswith("data:"):
        if marker not in value:
            raise ChatAttachmentError("invalid_file_payload")
        value = value.split(marker, 1)[1]
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ChatAttachmentError("invalid_file_payload") from exc


def _attachment_from_part(
    *,
    message_index: int,
    user_text: str,
    part: dict[str, Any],
) -> _Attachment | str:
    """The PDF in ``part``, or the filename of a file Klai cannot read as a PDF.

    Only a PDF can fail the turn (too large); anything that is not one,
    whatever its size or payload, is just a file to leave out.
    """
    file_info = part.get("file")
    if not isinstance(file_info, dict):
        return "document"
    filename = str(file_info.get("filename") or "document.pdf").strip() or "document.pdf"
    try:
        content = _extract_data_url_bytes(file_info.get("file_data"))
    except ChatAttachmentError:
        return filename
    if not content.startswith(b"%PDF"):
        return filename
    if len(content) > settings.chat_pdf_max_bytes:
        raise ChatAttachmentError("file_too_large")
    return _Attachment(
        message_index=message_index,
        filename=filename,
        content=content,
        user_text=user_text,
    )


def _split_file_parts(messages: list[dict[str, Any]]) -> tuple[list[_Attachment], list[str], list[dict[str, Any]]]:
    """The PDFs of the latest user turn, the filenames of its other files, and the messages without those files."""
    latest_index = _latest_user_message_index(messages)
    if latest_index is None:
        return [], [], messages
    latest = messages[latest_index]
    content = latest.get("content")
    if not isinstance(content, list):
        return [], [], messages

    user_text = _text_from_parts(content)
    attachments: list[_Attachment] = []
    unreadable: list[str] = []
    kept: list[Any] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "file":
            found = _attachment_from_part(message_index=latest_index, user_text=user_text, part=part)
            if isinstance(found, str):
                unreadable.append(found)
                continue
            attachments.append(found)
        kept.append(part)
    if not unreadable:
        return attachments, [], messages
    return (
        attachments,
        unreadable,
        [*messages[:latest_index], {**latest, "content": kept}, *messages[latest_index + 1 :]],
    )


def _strip_embedded_images(markdown: str) -> str:
    return _EMBEDDED_DATA_IMAGE_RE.sub(_IMAGE_PLACEHOLDER, markdown)


def _extract_docling_markdown(payload: object) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("chunks"), list):
        texts: list[str] = []
        for item in payload["chunks"]:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text = _strip_embedded_images(item["text"]).strip()
                if text:
                    texts.append(text)
        return "\n\n".join(texts).strip()

    document = payload.get("document") if isinstance(payload, dict) else None
    if isinstance(document, dict) and isinstance(document.get("md_content"), str):
        return _strip_embedded_images(document["md_content"]).strip()
    return ""


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


async def _convert_pdf_with_docling(attachment: _Attachment) -> str:
    try:
        submitted = await docling_client.submit_file_async(
            filename=attachment.filename,
            content=attachment.content,
            content_type="application/pdf",
            input_format="pdf",
        )
    except DoclingTimeoutError as exc:
        raise ChatAttachmentError("processing_timeout") from exc
    except DoclingError as exc:
        raise ChatAttachmentError("processing_failed") from exc

    deadline = time.monotonic() + settings.chat_pdf_conversion_timeout_s
    while True:
        if time.monotonic() >= deadline:
            raise ChatAttachmentError("processing_timeout")
        try:
            poll = await docling_client.poll_status(submitted.task_id)
        except DoclingTimeoutError as exc:
            raise ChatAttachmentError("processing_timeout") from exc
        except DoclingError as exc:
            raise ChatAttachmentError("unreadable_pdf") from exc
        if not poll.terminal:
            await asyncio.sleep(settings.chat_pdf_poll_interval_s)
            continue
        if poll.status != DoclingTaskStatus.SUCCESS:
            raise ChatAttachmentError("unreadable_pdf")
        break

    try:
        markdown = await docling_client.get_result_markdown(submitted.task_id)
    except DoclingError as exc:
        raise ChatAttachmentError("unreadable_pdf") from exc
    markdown = _strip_embedded_images(markdown).strip()
    if not markdown:
        raise ChatAttachmentError("unreadable_pdf")
    return markdown


def _replace_latest_user_content(
    messages: list[dict[str, Any]],
    *,
    message_index: int,
    user_text: str,
    filename: str,
    markdown: str,
) -> list[dict[str, Any]]:
    next_messages = list(messages)
    original = next_messages[message_index]
    next_messages[message_index] = {
        **original,
        "content": (
            f"{user_text.strip()}\n\n"
            f"{UPLOADED_PDF_CONTENT_MARKER}\n"
            f"Filename: {filename}\n\n"
            f"{markdown.strip()}\n"
            "[End uploaded PDF content]"
        ).strip(),
    }
    return next_messages


async def process_chat_attachments(
    messages: object, *, language: object, answer_beside_unreadable: bool = True
) -> ChatAttachmentResult:
    """Convert the active PDF file part in the latest user turn to text.

    No-op (processed_count=0) when there is no PDF file part. File parts that
    are not PDFs are dropped from the returned messages and named in
    ``unreadable``; they are no error by themselves, unless
    ``answer_beside_unreadable`` is False (the typed text is only a caption):
    then the turn ends with the ``unsupported_attachment`` text before any PDF
    is converted. ``language``
    is the already-decided response-language CODE (computed on the
    unmodified messages, before this call) — used only to render
    ``user_visible_error`` text, never to decide anything about the PDF
    itself.
    """
    if not isinstance(messages, list):
        return ChatAttachmentResult(messages=[], processed_count=0, meta=_default_meta())
    typed_messages = [m for m in messages if isinstance(m, dict)]
    if len(typed_messages) != len(messages):
        return ChatAttachmentResult(messages=typed_messages, processed_count=0, meta=_default_meta())

    meta = _default_meta()
    start = time.monotonic()
    unreadable: list[str] = []
    try:
        attachments, unreadable, typed_messages = _split_file_parts(typed_messages)
        if unreadable and not answer_beside_unreadable:
            raise ChatAttachmentError("unsupported_attachment")
        meta["chat_pdf_attachments_seen"] = len(attachments)
        if not attachments:
            return ChatAttachmentResult(messages=typed_messages, processed_count=0, meta=meta, unreadable=unreadable)
        if len(attachments) > 1:
            raise ChatAttachmentError("too_many_attachments")

        attachment = attachments[0]
        meta["chat_pdf_bytes"] = len(attachment.content)
        markdown = await _convert_pdf_with_docling(attachment)
        estimated_tokens = _estimate_tokens(markdown)
        if estimated_tokens > settings.chat_pdf_max_extracted_tokens:
            raise ChatAttachmentError("extracted_text_too_large")

        meta["chat_pdf_attachments_processed"] = 1
        meta["chat_pdf_extracted_chars"] = len(markdown)
        meta["chat_pdf_extracted_tokens_estimate"] = estimated_tokens
        meta["chat_pdf_processing_ms"] = int((time.monotonic() - start) * 1000)
        return ChatAttachmentResult(
            messages=_replace_latest_user_content(
                typed_messages,
                message_index=attachment.message_index,
                user_text=attachment.user_text,
                filename=attachment.filename,
                markdown=markdown,
            ),
            processed_count=1,
            meta=meta,
            unreadable=unreadable,
        )
    except ChatAttachmentError as exc:
        meta["chat_pdf_error_reason"] = exc.reason
        meta["chat_pdf_processing_ms"] = int((time.monotonic() - start) * 1000)
        return ChatAttachmentResult(
            messages=typed_messages,
            processed_count=0,
            meta=meta,
            user_visible_error=user_visible_error(exc.reason, language),
            unreadable=unreadable,
        )
