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
