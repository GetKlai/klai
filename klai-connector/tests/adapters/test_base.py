"""Tests for DocumentRef — SPEC-KB-CONNECTORS-001 Phase 1, R2.5.

Verifies that the new identifier-capture fields (sender_email,
mentioned_emails) are present with the correct defaults.
"""

from app.adapters.base import DocumentRef


class TestDocumentRefIdentifierFields:
    def test_document_ref_defaults(self) -> None:
        """DocumentRef constructed without identifier fields produces safe empty defaults."""
        ref = DocumentRef(path="p", ref="r", size=0, content_type="text/plain")

        assert ref.sender_email == ""
        assert ref.mentioned_emails == []

    def test_document_ref_with_identifiers(self) -> None:
        """DocumentRef stores sender_email and mentioned_emails when provided."""
        ref = DocumentRef(
            path="p",
            ref="r",
            size=0,
            content_type="text/plain",
            sender_email="user@example.com",
            mentioned_emails=["a@x.com", "b@y.com"],
        )

        assert ref.sender_email == "user@example.com"
        assert ref.mentioned_emails == ["a@x.com", "b@y.com"]

    def test_mentioned_emails_not_shared_across_instances(self) -> None:
        """Appending to one DocumentRef's mentioned_emails must not mutate another.

        This proves that field(default_factory=list) was used rather than a
        mutable class-level default.
        """
        ref_a = DocumentRef(path="a", ref="r", size=0, content_type="text/plain")
        ref_b = DocumentRef(path="b", ref="r", size=0, content_type="text/plain")

        ref_a.mentioned_emails.append("x@y.com")

        assert ref_b.mentioned_emails == []
