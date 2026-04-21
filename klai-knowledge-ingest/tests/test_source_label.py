"""Tests for source_label computation (SPEC-KB-021 Change 1)."""
import pytest

from knowledge_ingest.models import IngestRequest
from knowledge_ingest.source_label import compute_source_label as _compute_source_label


def _make_request(**kwargs) -> IngestRequest:
    defaults = {
        "org_id": "org-123",
        "kb_slug": "helpdesk",
        "path": "article.md",
        "content": "Some content.",
    }
    defaults.update(kwargs)
    return IngestRequest(**defaults)


def test_compute_source_label_webcrawl():
    """source_type=crawl + source_domain → domain as label."""
    req = _make_request(source_type="crawl", source_domain="help.mitel.nl")
    assert _compute_source_label(req) == "help.mitel.nl"


def test_compute_source_label_crawl_no_domain_falls_back_to_kb_slug():
    """source_type=crawl without source_domain → kb_slug fallback."""
    req = _make_request(source_type="crawl")
    assert _compute_source_label(req) == "helpdesk"


def test_compute_source_label_connector_with_connector_type():
    """source_type=connector + connector_type → connector_type as label."""
    req = _make_request(source_type="connector", connector_type="redcactus-wiki")
    assert _compute_source_label(req) == "redcactus-wiki"


def test_compute_source_label_connector_with_source_connector_id_fallback():
    """source_type=connector + no connector_type + source_connector_id → id as label."""
    req = _make_request(
        source_type="connector",
        source_connector_id="conn-abc123",
    )
    assert _compute_source_label(req) == "conn-abc123"


def test_compute_source_label_meeting_transcript():
    """content_type containing 'transcript' → 'meetings'."""
    req = _make_request(content_type="meeting_transcript")
    assert _compute_source_label(req) == "meetings"


def test_compute_source_label_1on1():
    """content_type containing '1on1' → 'meetings'."""
    req = _make_request(content_type="1on1_notes")
    assert _compute_source_label(req) == "meetings"


def test_compute_source_label_fallback():
    """No specific source fields → kb_slug as fallback."""
    req = _make_request()
    assert _compute_source_label(req) == "helpdesk"


def test_compute_source_label_docs_source_type_falls_back():
    """source_type=docs (not crawl/connector) → kb_slug."""
    req = _make_request(source_type="docs")
    assert _compute_source_label(req) == "helpdesk"


def test_source_label_in_extra_payload():
    """source_label must appear in extra_payload after _compute_source_label is called
    and the field is set in ingest.py logic. This test verifies the field value
    independently from the full ingest pipeline."""
    req = _make_request(source_type="crawl", source_domain="docs.voys.nl")
    label = _compute_source_label(req)
    # Simulate what ingest.py does
    extra_payload: dict = {}
    extra_payload["source_label"] = label
    assert extra_payload["source_label"] == "docs.voys.nl"
