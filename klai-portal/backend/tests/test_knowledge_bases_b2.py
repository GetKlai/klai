"""Tests for SPEC-TI-010B finding B-2: preview_crawl sends Zitadel org_id (str).

Before the fix, crawl_preview called:
    knowledge_ingest_client.preview_crawl(org_id=str(org.id), ...)

Using str(org.id) produces an integer-as-string like "42" instead of the Zitadel
resourceowner string like "362757920133283846". Knowledge-ingest uses this value as
a tenant key in Qdrant — sending the int PK means the KB context lookup targets the
wrong (empty) namespace.

After the fix:
    knowledge_ingest_client.preview_crawl(org_id=org.zitadel_org_id, ...)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_org() -> MagicMock:
    org = MagicMock()
    org.id = 42
    org.zitadel_org_id = "362757920133283846"
    return org


def _make_kb(org_id: int = 42) -> MagicMock:
    kb = MagicMock()
    kb.org_id = org_id
    kb.id = 1
    kb.slug = "my-kb"
    return kb


class TestCrawlPreviewUsesZitadelOrgId:
    @pytest.mark.asyncio
    async def test_preview_crawl_sends_zitadel_org_id(self) -> None:
        """crawl_preview must pass org.zitadel_org_id (Zitadel string) to knowledge-ingest,
        not str(org.id) (int-as-string).
        """
        org = _make_org()
        kb = _make_kb(org_id=org.id)
        captured_calls: list[dict] = []

        async def _fake_preview_crawl(
            url: str,
            content_selector=None,
            org_id: str = "",
            try_ai: bool = False,
            cookies=None,
        ) -> dict:
            captured_calls.append({"org_id": org_id, "url": url})
            return {"fit_markdown": "# Hello", "word_count": 2, "url": url}

        from app.api.app_knowledge_bases import CrawlPreviewRequest

        body = CrawlPreviewRequest(url="https://example.com")

        with (
            patch(
                "app.api.app_knowledge_bases._get_caller_org",
                new=AsyncMock(return_value=("caller-id", org, MagicMock())),
            ),
            patch(
                "app.api.app_knowledge_bases._get_kb_or_404",
                new=AsyncMock(return_value=kb),
            ),
            patch(
                "app.api.app_knowledge_bases._require_owner",
                new=AsyncMock(),
            ),
            patch(
                "app.api.app_knowledge_bases.knowledge_ingest_client.preview_crawl",
                new=_fake_preview_crawl,
            ),
        ):
            from app.api.app_knowledge_bases import crawl_preview

            await crawl_preview(
                kb_slug="my-kb",
                body=body,
                credentials=MagicMock(),
                db=AsyncMock(),
            )

        assert len(captured_calls) == 1, "preview_crawl must be called exactly once"
        sent_org_id = captured_calls[0]["org_id"]

        # Must be the Zitadel string, not the int PK as string
        assert sent_org_id == org.zitadel_org_id, (
            f"Expected zitadel_org_id='{org.zitadel_org_id}', got '{sent_org_id}'. "
            f"If you see '{org.id}' (int as str), the B-2 fix did not take effect."
        )
        assert sent_org_id != str(org.id), (
            f"org_id must NOT be str(org.id)='{org.id!s}' — it must be the Zitadel string."
        )

    @pytest.mark.asyncio
    async def test_int_pk_as_string_does_not_equal_zitadel_org_id(self) -> None:
        """Regression guard: int PK as string differs from Zitadel string."""
        org = _make_org()
        assert str(org.id) != org.zitadel_org_id, (
            "Test setup error: org.id as string must differ from org.zitadel_org_id"
        )
