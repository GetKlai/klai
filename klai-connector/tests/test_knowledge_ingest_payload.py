"""Tests for the knowledge-ingest payload builder.

Focus: source_type / source_domain derivation for web_crawler (SPEC-KB-021).
"""

from pathlib import Path
from runpy import run_path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.clients.knowledge_ingest import (
    MAX_INGEST_CONTENT_CHARS,
    KnowledgeIngestClient,
    _build_payload,
)


def _base_kwargs(**overrides) -> dict:
    kw = {
        "org_id": "100000000000000002",
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


@pytest.mark.asyncio
async def test_delete_connector_document_sends_scoped_internal_request():
    client = KnowledgeIngestClient("http://knowledge-ingest", "internal-secret")
    await client._client.aclose()
    response = MagicMock()
    response.raise_for_status = MagicMock()
    http_client = MagicMock()
    http_client.delete = AsyncMock(return_value=response)
    client._client = http_client

    await client.delete_connector_document(
        org_id="org-1",
        kb_slug="prices",
        source_connector_id="connector-1",
        source_ref="json-feed:connector-1:group-a",
    )

    http_client.delete.assert_awaited_once_with(
        "/ingest/v1/connector/document",
        params={
            "org_id": "org-1",
            "kb_slug": "prices",
            "connector_id": "connector-1",
            "source_ref": "json-feed:connector-1:group-a",
        },
        headers={
            "X-Internal-Secret": "internal-secret",
            "X-Caller-Service": "connector",
        },
    )
    response.raise_for_status.assert_called_once_with()


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
            org_id="100000000000000002",
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


@pytest.mark.asyncio
async def test_ingest_client_rejects_oversized_content_before_http_request() -> None:
    client = KnowledgeIngestClient(base_url="http://knowledge-ingest:8100", internal_secret="placeholder-secret")
    client._client.post = AsyncMock()

    with pytest.raises(ValueError, match=r"ingest limit is 500000 characters"):
        await client.ingest_document(
            org_id="org-1",
            kb_slug="support",
            path="feed.json",
            content="x" * (MAX_INGEST_CONTENT_CHARS + 1),
            source_connector_id="connector-1",
            source_ref="json-feed:connector-1",
        )

    client._client.post.assert_not_awaited()
    await client.aclose()


def test_client_content_limit_matches_knowledge_ingest_request_contract() -> None:
    models_path = Path(__file__).parents[2] / "klai-knowledge-ingest" / "knowledge_ingest" / "models.py"
    namespace = run_path(str(models_path))
    ingest_request = namespace["IngestRequest"]
    content_field = ingest_request.model_fields["content"]
    downstream_limit = next(
        metadata.max_length for metadata in content_field.metadata if getattr(metadata, "max_length", None) is not None
    )

    assert downstream_limit == MAX_INGEST_CONTENT_CHARS
