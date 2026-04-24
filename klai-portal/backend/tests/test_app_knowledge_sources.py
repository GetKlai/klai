"""Integration tests for the three source-ingest routes (SPEC-KB-SOURCES-001 Module 1).

Mirrors the pattern in test_app_knowledge_bases_quota.py: patch
``_get_caller_org``, mock DB queries, mock extractor + knowledge-ingest
client. Verifies wiring, error mapping per SPEC D8, and payload shape.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    SourceFetchError,
    SSRFBlockedError,
    UnsupportedSourceError,
)

# --- Fixtures ---------------------------------------------------------------


def _make_db_mock(kb: MagicMock | None = None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    kb_query_result = MagicMock()
    kb_query_result.scalar_one_or_none.return_value = kb
    db.execute.return_value = kb_query_result
    return db


def _make_org(plan: str = "complete") -> MagicMock:
    org = MagicMock()
    org.id = 1
    org.plan = plan
    org.slug = "voys"
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_kb(slug: str = "personal", owner_type: str = "user") -> MagicMock:
    kb = MagicMock()
    kb.id = 42
    kb.slug = slug
    kb.name = "Personal"
    kb.org_id = 1
    kb.owner_type = owner_type
    kb.owner_user_id = "user-abc"
    kb.created_by = "user-abc"
    kb.default_org_role = None
    return kb


class _CommonPatches:
    """Apply the common auth / role / quota patches via ExitStack."""

    def __init__(
        self,
        *,
        role: str = "owner",
        quota_ok: bool = True,
        extract_return: tuple | None = None,
        extract_side_effect: Exception | None = None,
        extract_target: str | None = None,  # "url", "youtube", or None for text
        ingest_return: str = "art-1",
        ingest_side_effect: Exception | None = None,
    ) -> None:
        self.role = role
        self.quota_ok = quota_ok
        self.extract_return = extract_return
        self.extract_side_effect = extract_side_effect
        self.extract_target = extract_target
        self.ingest_return = ingest_return
        self.ingest_side_effect = ingest_side_effect
        self.stack = ExitStack()
        self.mock_ingest: AsyncMock | None = None

    def __enter__(self) -> _CommonPatches:
        org = _make_org()
        caller_id = "user-abc"
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources._get_caller_org",
                AsyncMock(return_value=(caller_id, org, MagicMock())),
            )
        )
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.get_user_role_for_kb",
                AsyncMock(return_value=self.role),
            )
        )
        if self.quota_ok:
            self.stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.assert_can_add_item_to_kb",
                    AsyncMock(return_value=None),
                )
            )
        else:
            self.stack.enter_context(
                patch(
                    "app.api.app_knowledge_sources.assert_can_add_item_to_kb",
                    AsyncMock(
                        side_effect=HTTPException(
                            status_code=403,
                            detail={"error_code": "kb_quota_items_exceeded"},
                        )
                    ),
                )
            )

        if self.extract_target in {"url", "youtube"}:
            fn_name = f"extract_{self.extract_target}"
            target_path = f"app.api.app_knowledge_sources.{fn_name}"
            mock_extract = AsyncMock(
                return_value=self.extract_return,
                side_effect=self.extract_side_effect,
            )
            self.stack.enter_context(patch(target_path, mock_extract))

        self.mock_ingest = AsyncMock(
            return_value=self.ingest_return,
            side_effect=self.ingest_side_effect,
        )
        self.stack.enter_context(
            patch(
                "app.api.app_knowledge_sources.knowledge_ingest_client.ingest_document",
                self.mock_ingest,
            )
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stack.close()


# --- Text route -------------------------------------------------------------


class TestTextRoute:
    @pytest.mark.asyncio
    async def test_happy_path_returns_201_with_source_ref(self) -> None:
        from app.api.app_knowledge_sources import TextSourceRequest, add_text_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        body = TextSourceRequest(title="My note", content="Hello world")

        with _CommonPatches(ingest_return="art-123") as patches:
            resp = await add_text_source(
                kb_slug="personal",
                body=body,
                credentials=MagicMock(),
                db=db,
            )

        assert resp.artifact_id == "art-123"
        assert resp.source_type == "text"
        assert resp.source_ref.startswith("text:sha256:")
        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["source_type"] == "text"
        assert payload["content_type"] == "plain_text"
        assert payload["org_id"] == "zitadel-org-1"
        assert payload["kb_slug"] == "personal"
        assert payload["title"] == "My note"
        assert payload["source_ref"] == resp.source_ref
        assert payload["path"] == resp.source_ref
        assert payload["kb_name"] == "Personal"
        assert payload["extra"]["original_title"] == "My note"

    @pytest.mark.asyncio
    async def test_same_content_same_source_ref_dedup(self) -> None:
        """R4.4 / D7: same body = same source_ref regardless of title."""
        from app.api.app_knowledge_sources import TextSourceRequest, add_text_source

        kb = _make_kb()
        refs: list[str] = []
        for title in ("Title A", "Title B"):
            db = _make_db_mock(kb)
            with _CommonPatches(ingest_return="x"):
                resp = await add_text_source(
                    kb_slug="personal",
                    body=TextSourceRequest(title=title, content="same body"),
                    credentials=MagicMock(),
                    db=db,
                )
                refs.append(resp.source_ref)
        assert refs[0] == refs[1]

    @pytest.mark.asyncio
    async def test_returns_400_on_empty_content_after_normalisation(self) -> None:
        from app.api.app_knowledge_sources import TextSourceRequest, add_text_source

        kb = _make_kb()
        db = _make_db_mock(kb)
        body = TextSourceRequest(title=None, content=" \x00 \x00 ")

        with _CommonPatches(), pytest.raises(HTTPException) as exc:
            await add_text_source(
                kb_slug="personal",
                body=body,
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_404_on_unknown_kb(self) -> None:
        from app.api.app_knowledge_sources import TextSourceRequest, add_text_source

        db = _make_db_mock(kb=None)

        with _CommonPatches(), pytest.raises(HTTPException) as exc:
            await add_text_source(
                kb_slug="nonexistent",
                body=TextSourceRequest(title="t", content="body"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_403_on_viewer_role(self) -> None:
        from app.api.app_knowledge_sources import TextSourceRequest, add_text_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with _CommonPatches(role="viewer"), pytest.raises(HTTPException) as exc:
            await add_text_source(
                kb_slug="personal",
                body=TextSourceRequest(title="t", content="body"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_returns_403_when_kb_item_quota_exceeded(self) -> None:
        from app.api.app_knowledge_sources import TextSourceRequest, add_text_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with _CommonPatches(quota_ok=False), pytest.raises(HTTPException) as exc:
            await add_text_source(
                kb_slug="personal",
                body=TextSourceRequest(title="t", content="body"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 403
        assert exc.value.detail.get("error_code") == "kb_quota_items_exceeded"


# --- URL route --------------------------------------------------------------


class TestUrlRoute:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        from app.api.app_knowledge_sources import UrlSourceRequest, add_url_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with _CommonPatches(
            extract_target="url",
            extract_return=(
                "Example Domain",
                "# Example Domain\n\nBody",
                "https://example.com/",
            ),
            ingest_return="art-url-1",
        ) as patches:
            resp = await add_url_source(
                kb_slug="personal",
                body=UrlSourceRequest(url="https://example.com/"),
                credentials=MagicMock(),
                db=db,
            )

        assert resp.source_type == "url"
        assert resp.source_ref == "https://example.com/"
        assert resp.artifact_id == "art-url-1"
        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["source_type"] == "url"
        assert payload["content_type"] == "web_page"
        assert payload["source_ref"] == "https://example.com/"
        assert payload["extra"]["source_url"] == "https://example.com/"

    @pytest.mark.asyncio
    async def test_returns_400_on_invalid_url(self) -> None:
        from app.api.app_knowledge_sources import UrlSourceRequest, add_url_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with (
            _CommonPatches(
                extract_target="url",
                extract_side_effect=InvalidUrlError("bad"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await add_url_source(
                kb_slug="personal",
                body=UrlSourceRequest(url="ftp://bad"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_400_on_ssrf_blocked(self) -> None:
        from app.api.app_knowledge_sources import UrlSourceRequest, add_url_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with (
            _CommonPatches(
                extract_target="url",
                extract_side_effect=SSRFBlockedError("blocked"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await add_url_source(
                kb_slug="personal",
                body=UrlSourceRequest(url="http://localhost/"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 400
        assert "not allowed" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_returns_502_on_fetch_error(self) -> None:
        from app.api.app_knowledge_sources import UrlSourceRequest, add_url_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with (
            _CommonPatches(
                extract_target="url",
                extract_side_effect=SourceFetchError("network"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await add_url_source(
                kb_slug="personal",
                body=UrlSourceRequest(url="https://example.com/"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_returns_502_when_knowledge_ingest_unreachable(self) -> None:
        from app.api.app_knowledge_sources import UrlSourceRequest, add_url_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with (
            _CommonPatches(
                extract_target="url",
                extract_return=("T", "body", "https://example.com/"),
                ingest_side_effect=httpx.ConnectError("refused"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await add_url_source(
                kb_slug="personal",
                body=UrlSourceRequest(url="https://example.com/"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 502


# --- YouTube route ----------------------------------------------------------


class TestYoutubeRoute:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        from app.api.app_knowledge_sources import YouTubeSourceRequest, add_youtube_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with _CommonPatches(
            extract_target="youtube",
            extract_return=(
                "Rick Roll",
                "never gonna give you up",
                "youtube:dQw4w9WgXcQ",
            ),
            ingest_return="art-yt-1",
        ) as patches:
            resp = await add_youtube_source(
                kb_slug="personal",
                body=YouTubeSourceRequest(url="https://youtu.be/dQw4w9WgXcQ"),
                credentials=MagicMock(),
                db=db,
            )

        assert resp.source_type == "youtube"
        assert resp.source_ref == "youtube:dQw4w9WgXcQ"
        assert patches.mock_ingest is not None
        payload = patches.mock_ingest.call_args.args[0]
        assert payload["source_type"] == "youtube"
        assert payload["content_type"] == "youtube_transcript"
        assert payload["extra"]["video_id"] == "dQw4w9WgXcQ"
        assert payload["extra"]["source_url"] == "https://youtu.be/dQw4w9WgXcQ"

    @pytest.mark.asyncio
    async def test_returns_400_on_invalid_url(self) -> None:
        from app.api.app_knowledge_sources import YouTubeSourceRequest, add_youtube_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with (
            _CommonPatches(
                extract_target="youtube",
                extract_side_effect=InvalidUrlError("nope"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await add_youtube_source(
                kb_slug="personal",
                body=YouTubeSourceRequest(url="https://vimeo.com/x"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_422_on_no_transcript(self) -> None:
        from app.api.app_knowledge_sources import YouTubeSourceRequest, add_youtube_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with (
            _CommonPatches(
                extract_target="youtube",
                extract_side_effect=UnsupportedSourceError("no transcript"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await add_youtube_source(
                kb_slug="personal",
                body=YouTubeSourceRequest(url="https://youtu.be/noTranscrip"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_502_on_source_fetch_error(self) -> None:
        """IP block / rate-limit on YouTube → 502, NOT 422 (SPEC-KB-SOURCES-001 v1.3)."""
        from app.api.app_knowledge_sources import YouTubeSourceRequest, add_youtube_source

        kb = _make_kb()
        db = _make_db_mock(kb)

        with (
            _CommonPatches(
                extract_target="youtube",
                extract_side_effect=SourceFetchError("YouTube refused the request"),
            ),
            pytest.raises(HTTPException) as exc,
        ):
            await add_youtube_source(
                kb_slug="personal",
                body=YouTubeSourceRequest(url="https://youtu.be/dQw4w9WgXcQ"),
                credentials=MagicMock(),
                db=db,
            )
        assert exc.value.status_code == 502
        assert "youtube" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_source_ref_dedup_across_url_variants(self) -> None:
        """R3.5: same video via different URL shapes hits the same source_ref."""
        from app.api.app_knowledge_sources import YouTubeSourceRequest, add_youtube_source

        kb = _make_kb()
        refs: list[str] = []
        for url in (
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        ):
            db = _make_db_mock(kb)
            with _CommonPatches(
                extract_target="youtube",
                extract_return=("T", "body", "youtube:dQw4w9WgXcQ"),
                ingest_return="x",
            ):
                resp = await add_youtube_source(
                    kb_slug="personal",
                    body=YouTubeSourceRequest(url=url),
                    credentials=MagicMock(),
                    db=db,
                )
                refs.append(resp.source_ref)
        assert len(set(refs)) == 1
