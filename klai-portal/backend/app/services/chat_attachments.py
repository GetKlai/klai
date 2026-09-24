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
from dataclasses import dataclass
from typing import Any

from klai_chat_prompts import _language_is_dutch as language_is_dutch

from app.core.config import settings
from app.services import docling_client
from app.services.docling_client import DoclingError, DoclingTaskStatus, DoclingTimeoutError

_IMAGE_PLACEHOLDER = "<!-- image -->"
_EMBEDDED_DATA_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(data:image/[^)]*;base64,[^)]+\)")


@dataclass(frozen=True)
class ChatAttachmentResult:
    messages: list[dict[str, Any]]
    processed_count: int
    meta: dict[str, Any]
    user_visible_error: str | None = None


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
) -> _Attachment:
    file_info = part.get("file")
    if not isinstance(file_info, dict):
        raise ChatAttachmentError("invalid_file_payload")
    filename = str(file_info.get("filename") or "document.pdf").strip() or "document.pdf"
    content = _extract_data_url_bytes(file_info.get("file_data"))
    if len(content) > settings.chat_pdf_max_bytes:
        raise ChatAttachmentError("file_too_large")
    if not content.startswith(b"%PDF"):
        raise ChatAttachmentError("unsupported_attachment")
    return _Attachment(
        message_index=message_index,
        filename=filename,
        content=content,
        user_text=user_text,
    )


def _active_pdf_attachments(messages: list[dict[str, Any]]) -> list[_Attachment]:
    latest_index = _latest_user_message_index(messages)
    if latest_index is None:
        return []
    latest = messages[latest_index]
    content = latest.get("content")
    if not isinstance(content, list):
        return []

    user_text = _text_from_parts(content)
    attachments: list[_Attachment] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "file":
            continue
        attachments.append(
            _attachment_from_part(
                message_index=latest_index,
                user_text=user_text,
                part=part,
            )
        )
    return attachments


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
            "[Uploaded PDF content]\n"
            f"Filename: {filename}\n\n"
            f"{markdown.strip()}\n"
            "[End uploaded PDF content]"
        ).strip(),
    }
    return next_messages


async def process_chat_attachments(messages: object, *, language: object) -> ChatAttachmentResult:
    """Convert the active PDF file part in the latest user turn to text.

    No-op (processed_count=0) when there is no PDF file part. ``language``
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
    try:
        attachments = _active_pdf_attachments(typed_messages)
        meta["chat_pdf_attachments_seen"] = len(attachments)
        if not attachments:
            return ChatAttachmentResult(messages=typed_messages, processed_count=0, meta=meta)
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
        )
    except ChatAttachmentError as exc:
        meta["chat_pdf_error_reason"] = exc.reason
        meta["chat_pdf_processing_ms"] = int((time.monotonic() - start) * 1000)
        return ChatAttachmentResult(
            messages=typed_messages,
            processed_count=0,
            meta=meta,
            user_visible_error=user_visible_error(exc.reason, language),
        )
