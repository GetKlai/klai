"""Tests for the knowledge-ingest payload builder.

Focus: source_type / source_domain derivation for web_crawler (SPEC-KB-021).
"""

from app.clients.knowledge_ingest import _build_payload


def _base_kwargs(**overrides) -> dict:
    kw = {
        "org_id": "368884765035593759",
        "kb_slug": "support",
        "path": "index.md",
        "content": "hello",
        "source_connector_id": "414d4f82-f702-4ff2-abd4-c5ce38ae7d61",
        "source_ref": "https://help.voys.nl/",
        "source_url": "https://help.voys.nl/",
        "content_type": "kb_article",
        "connector_type": "web_crawler",
    }
    kw.update(overrides)
    return kw


class TestWebCrawlerSourceLabel:
    def test_web_crawler_sets_source_type_crawl(self):
        payload = _build_payload(**_base_kwargs())
        assert payload["source_type"] == "crawl"

    def test_web_crawler_sets_source_domain_from_url(self):
        payload = _build_payload(**_base_kwargs())
        assert payload["source_domain"] == "help.voys.nl"

    def test_web_crawler_with_subpath_still_uses_hostname(self):
        payload = _build_payload(**_base_kwargs(source_url="https://docs.example.com/a/b/c"))
        assert payload["source_domain"] == "docs.example.com"

    def test_web_crawler_without_source_url_omits_domain(self):
        payload = _build_payload(**_base_kwargs(source_url=""))
        assert payload["source_type"] == "crawl"
        assert "source_domain" not in payload

    def test_non_crawl_connector_uses_connector_source_type(self):
        payload = _build_payload(**_base_kwargs(connector_type="github"))
        assert payload["source_type"] == "connector"
        assert "source_domain" not in payload

    def test_notion_connector_uses_connector_source_type(self):
        payload = _build_payload(**_base_kwargs(connector_type="notion"))
        assert payload["source_type"] == "connector"
        assert "source_domain" not in payload


class TestImageUrlsDedupInPayload:
    def test_duplicate_image_urls_collapsed(self):
        payload = _build_payload(
            **_base_kwargs(
                image_urls=[
                    "/kb-images/a.png",
                    "/kb-images/a.png",
                    "/kb-images/b.png",
                    "/kb-images/a.png",
                ],
            ),
        )
        assert payload["extra"]["image_urls"] == ["/kb-images/a.png", "/kb-images/b.png"]

    def test_empty_image_urls_omitted(self):
        payload = _build_payload(**_base_kwargs(image_urls=None))
        assert "image_urls" not in payload.get("extra", {})


class TestExtraPassthrough:
    def test_source_url_in_extra(self):
        payload = _build_payload(**_base_kwargs())
        assert payload["extra"]["source_url"] == "https://help.voys.nl/"

    def test_connector_type_preserved(self):
        payload = _build_payload(**_base_kwargs())
        assert payload["connector_type"] == "web_crawler"


class TestSenderEmailAndMentionedEmails:
    """SPEC-KB-CONNECTORS-001 Phase 1, R2.5 — identifier-capture fields."""

    def test_build_payload_includes_sender_email(self):
        payload = _build_payload(**_base_kwargs(sender_email="x@y.com"))
        assert payload["extra"]["sender_email"] == "x@y.com"

    def test_build_payload_includes_mentioned_emails(self):
        payload = _build_payload(**_base_kwargs(mentioned_emails=["a@b.com"]))
        assert payload["extra"]["mentioned_emails"] == ["a@b.com"]

    def test_build_payload_empty_sender_email_not_in_extra(self):
        payload = _build_payload(**_base_kwargs(sender_email=""))
        assert "sender_email" not in payload.get("extra", {})

    def test_build_payload_empty_mentioned_emails_not_in_extra(self):
        payload = _build_payload(**_base_kwargs(mentioned_emails=None))
        assert "mentioned_emails" not in payload.get("extra", {})

    def test_build_payload_empty_list_mentioned_emails_not_in_extra(self):
        payload = _build_payload(**_base_kwargs(mentioned_emails=[]))
        assert "mentioned_emails" not in payload.get("extra", {})

    def test_build_payload_backward_compatible(self):
        """Calling _build_payload without sender_email/mentioned_emails still works."""
        payload = _build_payload(
            org_id="368884765035593759",
            kb_slug="support",
            path="index.md",
            content="hello",
            source_connector_id="414d4f82-f702-4ff2-abd4-c5ce38ae7d61",
            source_ref="https://help.voys.nl/",
            source_url="https://help.voys.nl/",
            content_type="kb_article",
            connector_type="web_crawler",
        )
        assert "sender_email" not in payload.get("extra", {})
        assert "mentioned_emails" not in payload.get("extra", {})
