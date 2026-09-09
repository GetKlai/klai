import klai_chat_attachments as attachments


def test_extract_docling_chunks_strips_embedded_base64_images():
    markdown = attachments._extract_docling_markdown(
        {
            "chunks": [
                {
                    "text": (
                        "Intro\n"
                        "![scan](data:image/png;base64,"
                        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ)\n"
                        "Outro"
                    )
                }
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
        {
            "document": {
                "md_content": (
                    "# Titel\n"
                    "![diagram](data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD)\n"
                    "Tekst"
                )
            }
        }
    )

    assert "# Titel" in markdown
    assert "Tekst" in markdown
    assert "<!-- image -->" in markdown
    assert "data:image/" not in markdown
    assert "/9j/4AAQ" not in markdown


def test_user_visible_error_follows_language_code_not_query_text():
    assert attachments.user_visible_error("file_too_large", "nl").startswith(
        "Deze PDF is te groot"
    )
    # No decision (None, empty, "und") falls back to Dutch, like every
    # other rendered string in the chat stack.
    assert attachments.user_visible_error("file_too_large", None).startswith(
        "Deze PDF is te groot"
    )
    assert attachments.user_visible_error("file_too_large", "und").startswith(
        "Deze PDF is te groot"
    )
    assert attachments.user_visible_error("file_too_large", "").startswith(
        "Deze PDF is te groot"
    )
    # Any other explicit code is English, even "de" (Dutch variant codes
    # are not the contract; same rule as klai_chat_prompts).
    assert attachments.user_visible_error("file_too_large", "en").startswith(
        "This PDF is too large"
    )
    assert attachments.user_visible_error("file_too_large", "de").startswith(
        "This PDF is too large"
    )


def test_attachment_error_ignores_dutch_substrings_in_query():
    # The old heuristic keyed on " pdf "/" bestand " inside the query; the
    # conversation decision now owns the choice.
    error = attachments.user_visible_error("unreadable_pdf", "en")
    assert "This PDF does not contain readable text" in error
    assert attachments.user_visible_error("unreadable_pdf", None) == (
        "Deze PDF bevat geen leesbare tekst die Klai direct kan gebruiken."
    )


def test_substring_language_heuristic_is_gone():
    assert not hasattr(attachments, "_looks_dutch")
