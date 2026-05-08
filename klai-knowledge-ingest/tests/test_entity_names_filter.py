"""Unit tests for filter_entity_names_for_chunk in qdrant_store.

The pure helper decides which document-level entity names belong on a specific
chunk. Pollution of BM25 with off-section names is exactly what this filter is
designed to prevent — these tests lock in the expected behavior.
"""

from knowledge_ingest.qdrant_store import filter_entity_names_for_chunk


def test_returns_only_names_present_in_chunk() -> None:
    chunk = "Voys Freedom koppelen aan Bubble via RedCactus integratie."
    doc_names = ["Voys", "Bubble", "RedCactus", "Salesforce", "WhatsApp"]

    result = filter_entity_names_for_chunk(chunk, doc_names)

    # Salesforce and WhatsApp belong to a different section of the same doc.
    assert "Voys" in result
    assert "Bubble" in result
    assert "RedCactus" in result
    assert "Salesforce" not in result
    assert "WhatsApp" not in result


def test_case_insensitive_match() -> None:
    chunk = "Configure your salesforce instance with VOYS api credentials."
    doc_names = ["Salesforce", "Voys"]

    result = filter_entity_names_for_chunk(chunk, doc_names)

    assert "Salesforce" in result
    assert "Voys" in result


def test_skips_short_names_to_avoid_false_positives() -> None:
    chunk = "If you fail to authenticate, the AI assistant explains the error."
    # "AI" appears in "fail" — without min-length filter we'd false-positive.
    doc_names = ["AI", "OK", "ZZ", "Salesforce"]

    result = filter_entity_names_for_chunk(chunk, doc_names)

    assert "AI" not in result
    assert "OK" not in result
    assert "ZZ" not in result
    assert "Salesforce" not in result  # not in this chunk


def test_three_char_names_are_kept() -> None:
    chunk = "Configure CRM and ERP integrations with SSO."
    doc_names = ["CRM", "ERP", "SSO"]

    result = filter_entity_names_for_chunk(chunk, doc_names)

    assert result == ["CRM", "ERP", "SSO"]


def test_dedupe_when_graphiti_emitted_multiple_casings() -> None:
    chunk = "Voys Freedom integration with Voys phone systems."
    doc_names = ["Voys", "voys", "VOYS"]

    result = filter_entity_names_for_chunk(chunk, doc_names)

    assert len(result) == 1
    assert result[0].lower() == "voys"


def test_multi_word_entities_match_via_substring() -> None:
    chunk = "We support Microsoft Teams via the Vexa connector."
    doc_names = ["Microsoft Teams", "Vexa"]

    result = filter_entity_names_for_chunk(chunk, doc_names)

    assert "Microsoft Teams" in result
    assert "Vexa" in result


def test_empty_chunk_returns_empty() -> None:
    result = filter_entity_names_for_chunk("", ["Voys", "Salesforce"])
    assert result == []


def test_empty_doc_names_returns_empty() -> None:
    result = filter_entity_names_for_chunk("Some text about Voys.", [])
    assert result == []


def test_skips_blank_or_whitespace_names() -> None:
    chunk = "Voys integration documentation."
    doc_names = ["Voys", "", "   ", "  Voys  "]

    result = filter_entity_names_for_chunk(chunk, doc_names)

    # Strip + dedup yields one canonical "Voys".
    assert len(result) == 1
    assert result[0].lower() == "voys"


def test_skips_non_string_inputs() -> None:
    chunk = "Voys integration documentation."
    # Defensive: graphiti results have always been strings, but type defensiveness
    # is cheap and prevents a future bug from blowing up the ingest path.
    doc_names = ["Voys", None, 42, ["nested"]]  # type: ignore[list-item]

    result = filter_entity_names_for_chunk(chunk, doc_names)  # type: ignore[arg-type]

    assert result == ["Voys"]
