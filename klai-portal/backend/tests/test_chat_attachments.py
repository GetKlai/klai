"""Moved from deploy/litellm/tests/test_klai_chat_attachments.py (one-chat-
pipeline slice 3) — pins the pure helpers of app.services.chat_attachments."""

import base64

import pytest

from app.services import chat_attachments as attachments
from app.services import docling_client


def test_extract_docling_chunks_strips_embedded_base64_images():
    markdown = attachments._extract_docling_markdown(
        {
            "chunks": [
                {"text": ("Intro\n![scan](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ)\nOutro")}
            ]
        }
    )

    assert "Intro" in markdown
    assert "Outro" in markdown
    assert "<!-- image -->" in markdown
    assert "data:image/" not in markdown
    assert "iVBORw0KGgo" not in markdown


def test_extract_docling_markdown_strips_embedded_base64_images():
    markdown = attachments._extract_docling_markdown(
        {"document": {"md_content": ("# Titel\n![diagram](data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD)\nTekst")}}
    )

    assert "# Titel" in markdown
    assert "Tekst" in markdown
    assert "<!-- image -->" in markdown
    assert "data:image/" not in markdown
    assert "/9j/4AAQ" not in markdown


def test_user_visible_error_follows_language_code_not_query_text():
    assert attachments.user_visible_error("file_too_large", "nl").startswith("Deze PDF is te groot")
    # No decision (None, empty, "und") falls back to Dutch, like every
    # other rendered string in the chat stack.
    assert attachments.user_visible_error("file_too_large", None).startswith("Deze PDF is te groot")
    assert attachments.user_visible_error("file_too_large", "und").startswith("Deze PDF is te groot")
    assert attachments.user_visible_error("file_too_large", "").startswith("Deze PDF is te groot")
    # Any other explicit code is English, even "de" (Dutch variant codes
    # are not the contract; same rule as klai_chat_prompts).
    assert attachments.user_visible_error("file_too_large", "en").startswith("This PDF is too large")
    assert attachments.user_visible_error("file_too_large", "de").startswith("This PDF is too large")


def test_attachment_error_ignores_dutch_substrings_in_query():
    # The old heuristic keyed on " pdf "/" bestand " inside the query; the
    # conversation decision now owns the choice.
    error = attachments.user_visible_error("unreadable_pdf", "en")
    assert "This PDF does not contain readable text" in error
    assert attachments.user_visible_error("unreadable_pdf", None) == (
        "Deze PDF bevat geen leesbare tekst die Klai direct kan gebruiken."
    )


def _pdf_data_url() -> str:
    payload = base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode()
    return f"data:application/pdf;base64,{payload}"


@pytest.mark.asyncio
async def test_process_chat_attachments_converts_pdf_before_retrieval(monkeypatch):
    """New test (not in the hook's suite): pins the acceptance criterion that
    a PDF file part is converted to text before it reaches retrieval/generation."""

    async def fake_submit(*, filename, content, content_type, input_format=None):
        return docling_client.DoclingSubmitResult(task_id="task-1", initial_status="pending")

    async def fake_poll(task_id):
        return docling_client.DoclingPollResult(
            task_id=task_id, status="success", terminal=True, error_message=None, queue_position=None
        )

    async def fake_markdown(task_id):
        return "Extracted PDF body text."

    monkeypatch.setattr(attachments.docling_client, "submit_file_async", fake_submit)
    monkeypatch.setattr(attachments.docling_client, "poll_status", fake_poll)
    monkeypatch.setattr(attachments.docling_client, "get_result_markdown", fake_markdown)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Please summarise this document."},
                {"type": "file", "file": {"filename": "report.pdf", "file_data": _pdf_data_url()}},
            ],
        }
    ]

    result = await attachments.process_chat_attachments(messages, language="en")

    assert result.processed_count == 1
    assert result.user_visible_error is None
    final_content = result.messages[0]["content"]
    assert isinstance(final_content, str)
    assert "Please summarise this document." in final_content
    assert "Extracted PDF body text." in final_content
    assert "[Uploaded PDF content]" in final_content


@pytest.mark.asyncio
async def test_process_chat_attachments_is_noop_without_a_file_part():
    messages = [{"role": "user", "content": "Just a question, no attachment."}]

    result = await attachments.process_chat_attachments(messages, language="en")

    assert result.processed_count == 0
    assert result.messages == messages
