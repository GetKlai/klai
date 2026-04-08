"""Tests for image extraction from Unstructured partition results."""

from unittest.mock import MagicMock, patch

from app.services.parser import parse_document_with_images


class TestParseDocumentWithImages:
    """Tests for the extended parser that also extracts images."""

    def test_plain_text_returns_no_images(self):
        """Text files have no embedded images."""
        result = parse_document_with_images(b"Hello world", "readme.md")
        assert result.text == "Hello world"
        assert result.images == []

    def test_plain_text_utf8_decode(self):
        """Text is UTF-8 decoded as before."""
        result = parse_document_with_images(b"Hallo wereld", "notes.txt")
        assert result.text == "Hallo wereld"

    @patch("app.services.parser.partition")
    def test_binary_format_extracts_text_and_images(self, mock_partition):
        """PDF/DOCX partitioning returns both text elements and Image elements."""
        text_elem = MagicMock()
        text_elem.__str__ = lambda self: "Some paragraph text"
        text_elem.category = "NarrativeText"
        text_elem.metadata = MagicMock(image_base64=None)

        image_elem = MagicMock()
        image_elem.__str__ = lambda self: ""
        image_elem.category = "Image"
        image_elem.metadata = MagicMock(
            image_base64="aVZCT1J3MEtHZ29BQUFBTg==",
            image_mime_type="image/png",
        )

        mock_partition.return_value = [text_elem, image_elem]

        result = parse_document_with_images(b"fake pdf bytes", "document.pdf")

        assert "Some paragraph text" in result.text
        assert len(result.images) == 1
        assert result.images[0]["mime_type"] == "image/png"
        assert result.images[0]["data_b64"] == "aVZCT1J3MEtHZ29BQUFBTg=="

    @patch("app.services.parser.partition")
    def test_binary_format_skips_images_without_base64(self, mock_partition):
        """Image elements without base64 data are skipped."""
        image_elem = MagicMock()
        image_elem.__str__ = lambda self: "[image]"
        image_elem.category = "Image"
        image_elem.metadata = MagicMock(image_base64=None, image_mime_type=None)

        mock_partition.return_value = [image_elem]

        result = parse_document_with_images(b"fake bytes", "doc.docx")

        assert result.images == []


class TestKnowledgeIngestClientImageUrls:
    """Tests for the image_urls parameter in KnowledgeIngestClient."""

    def test_payload_includes_image_urls_when_provided(self):
        """image_urls should be added to the extra dict in the payload."""
        from app.clients.knowledge_ingest import KnowledgeIngestClient

        client = KnowledgeIngestClient(base_url="http://fake:8100")

        # Access the internal payload building logic by inspecting what would be sent.
        # We test indirectly via the public method signature accepting image_urls.
        import inspect
        sig = inspect.signature(client.ingest_document)
        assert "image_urls" in sig.parameters

    def test_payload_extra_merges_source_url_and_images(self):
        """When both source_url and image_urls are provided, extra contains both."""
        from app.clients.knowledge_ingest import _build_payload

        payload = _build_payload(
            org_id="org-1",
            kb_slug="kb-1",
            path="doc.md",
            content="text",
            source_connector_id="conn-1",
            source_ref="ref",
            source_url="https://example.com/doc",
            content_type="kb_article",
            image_urls=["https://s3/img1.png", "https://s3/img2.png"],
        )

        assert payload["extra"]["source_url"] == "https://example.com/doc"
        assert payload["extra"]["image_urls"] == ["https://s3/img1.png", "https://s3/img2.png"]

    def test_payload_extra_omits_image_urls_when_empty(self):
        """When image_urls is None, extra should not contain the key."""
        from app.clients.knowledge_ingest import _build_payload

        payload = _build_payload(
            org_id="org-1",
            kb_slug="kb-1",
            path="doc.md",
            content="text",
            source_connector_id="conn-1",
            source_ref="ref",
            source_url="https://example.com/doc",
            content_type="kb_article",
            image_urls=None,
        )

        assert "image_urls" not in payload.get("extra", {})
