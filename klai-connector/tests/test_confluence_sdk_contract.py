"""Confluence adapter contract against the *real* atlassian-python-api client.

GetKlai/klai#1137. ``tests/test_confluence_ssrf_legacy.py`` patches the SDK
class, which is exactly why CI stayed green when the v5 lock refresh removed
``get_page_by_id`` from the Cloud client: patching the class means the adapter
never touches SDK code, so an ``AttributeError`` there cannot surface.

Neither suite in this file patches the SDK.

``TestConfluenceSdkContract`` binds the adapter's real call signatures against
an un-patched ``ConfluenceV2`` instance — a renamed or removed method fails
here, in CI, instead of in production.

``TestConfluenceV2HttpContract`` patches only ``requests.Session.request``, the
network boundary. Every line of SDK URL-building and response-parsing runs for
real against recorded Confluence Cloud v2 payloads, so endpoint-path drift and
response-shape drift both fail here too.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from klai_image_storage.url_guard import _reset_dns_cache

from app.adapters.confluence import (
    _MAX_PAGES_PER_SPACE,
    _MAX_SPACES,
    _PAGE_BATCH,
    ConfluenceAdapter,
    _build_confluence_client,
)

BASE_URL = "https://klai.atlassian.net"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _reset_dns_cache()


def _connector(space_keys: list[str] | None = None) -> SimpleNamespace:
    config: dict[str, Any] = {
        "base_url": BASE_URL,
        "email": "x@y.com",
        "api_token": "t",
    }
    if space_keys is not None:
        config["space_keys"] = space_keys
    return SimpleNamespace(id="abc-123", config=config)


class TestConfluenceSdkContract:
    """The SDK surface ``ConfluenceAdapter`` depends on, asserted for real."""

    def _client(self) -> Any:
        return _build_confluence_client(BASE_URL, "x@y.com", "token")

    def test_client_targets_cloud_v2(self) -> None:
        client = self._client()
        # ConfluenceV2 appends the /wiki context path itself.
        assert client.url == f"{BASE_URL}/wiki"
        assert str(client.api_version) == "2"

    @pytest.mark.parametrize(
        ("method_name", "args", "kwargs"),
        [
            # fetch_document()
            ("get_page_by_id", ("12345",), {"body_format": "storage"}),
            # _paginate_v2(), used by both listings
            ("get", ("api/v2/pages",), {"params": {"limit": _PAGE_BATCH}}),
            ("get_endpoint", ("spaces",), {}),
            ("get_endpoint", ("page",), {}),
        ],
    )
    def test_adapter_call_binds_against_real_client(
        self, method_name: str, args: tuple, kwargs: dict
    ) -> None:
        """Every call the adapter makes must exist and accept its arguments.

        This is the check that fails on the next breaking major bump. v5
        removed ``get_page_by_id`` from the Cloud client and renamed the space
        and page listings; each of those would trip this test.

        The listings go through ``get_endpoint`` + ``get`` rather than
        ``get_pages`` / ``get_spaces``: those helpers page to the end before
        returning, so they cannot honour a cap. Endpoint NAMES still come from
        the SDK, which is what keeps this a contract rather than a hardcoded
        URL.
        """
        client = self._client()
        method = getattr(client, method_name, None)
        assert method is not None, (
            f"ConfluenceV2 no longer exposes {method_name!r} — "
            "app/adapters/confluence.py calls it"
        )
        # bind() raises TypeError when the parameter names have drifted.
        inspect.signature(method).bind(*args, **kwargs)


    def test_endpoint_keys_resolve_to_the_v2_paths(self) -> None:
        """The SDK must still map our endpoint keys to the v2 collections."""
        client = self._client()
        assert client.get_endpoint("spaces") == "api/v2/spaces"
        assert client.get_endpoint("page") == "api/v2/pages"


def _response(payload: dict[str, Any], status: int = 200) -> requests.Response:
    """Build a genuine ``requests.Response`` so the SDK parses it for real."""
    resp = requests.Response()
    resp.status_code = status
    resp.reason = "OK"
    resp.headers["Content-Type"] = "application/json"
    resp._content = json.dumps(payload).encode("utf-8")
    return resp


class _FakeConfluenceCloud:
    """Confluence Cloud v2 served over the ``requests`` boundary.

    Records every URL the SDK builds, so an endpoint-path regression is
    visible in the assertions rather than silently mocked away.
    """

    def __init__(
        self,
        spaces: list[dict[str, Any]] | None = None,
        pages: list[dict[str, Any]] | None = None,
        storage_body: str = "",
        page_list_status: int = 200,
        page_size: int | None = None,
        href_form_cursor: bool = False,
    ) -> None:
        self.spaces = spaces if spaces is not None else []
        self.pages = pages if pages is not None else []
        self.storage_body = storage_body
        self.page_list_status = page_list_status
        self.page_size = page_size
        self.href_form_cursor = href_form_cursor
        self.urls: list[str] = []

    def _collection(
        self, url: str, items: list[dict[str, Any]], endpoint: str
    ) -> requests.Response:
        """Serve one cursor page, advertising the next as v2 does."""
        query = parse_qs(urlparse(url).query)
        offset = int(query.get("cursor", ["0"])[0])
        size = self.page_size or len(items) or 1
        window = items[offset : offset + size]
        payload: dict[str, Any] = {"results": window}
        if offset + size < len(items):
            nxt = f"{endpoint}?cursor={offset + size}&limit={size}"
            payload["_links"] = {
                "next": {"href": nxt} if self.href_form_cursor else nxt
            }
        return _response(payload)

    def handle(self, url: str) -> requests.Response:
        self.urls.append(url)
        path = url.split("?", 1)[0]

        if path.endswith("/wiki/api/v2/spaces"):
            return self._collection(url, self.spaces, "/wiki/api/v2/spaces")
        if path.endswith("/wiki/api/v2/pages"):
            if self.page_list_status != 200:
                return _response({"message": "boom"}, status=self.page_list_status)
            return self._collection(url, self.pages, "/wiki/api/v2/pages")
        if "/wiki/api/v2/pages/" in path:
            page_id = path.rsplit("/", 1)[-1]
            return _response(
                {
                    "id": page_id,
                    "title": "Page",
                    "body": {
                        "storage": {
                            "value": self.storage_body,
                            "representation": "storage",
                        }
                    },
                }
            )
        return _response({"message": f"unexpected path {path}"}, status=404)

    def as_session_request(self):
        """Return a ``requests.Session.request`` replacement bound to this fake."""
        fake = self

        def _request(_self: Any, method: str = "GET", url: str = "", **_: Any):
            return fake.handle(url)

        return _request


def _page(page_id: str, version_created: str = "2026-08-01T10:00:00.000Z") -> dict:
    """A Confluence Cloud v2 page object as returned by GET /wiki/api/v2/pages."""
    return {
        "id": page_id,
        "status": "current",
        "title": f"Page {page_id}",
        "spaceId": "9001",
        "authorId": "5b10ac8d82e05b22cc7d4ef5",
        "createdAt": "2026-07-01T09:00:00.000Z",
        "version": {
            "createdAt": version_created,
            "number": 3,
            "minorEdit": False,
            "authorId": "5b10ac8d82e05b22cc7d4ef5",
        },
        "_links": {"webui": f"/spaces/ENG/pages/{page_id}/Page"},
    }


@pytest.mark.asyncio
class TestConfluenceV2HttpContract:
    """Drive the adapter through real SDK code, faking only the network."""

    def _patches(self, fake: _FakeConfluenceCloud):
        return (
            patch.object(requests.Session, "request", fake.as_session_request()),
            patch(
                "klai_image_storage.url_guard._resolve_blocking",
                return_value=("104.192.136.1",),
            ),
        )

    async def test_list_documents_uses_v2_endpoints_and_maps_pages(self) -> None:
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9001", "key": "ENG", "name": "Engineering"}],
            pages=[_page("111"), _page("222", "2026-08-05T12:30:00.000Z")],
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(_connector())

        # The v2 endpoints, not the retired v1 /rest/api/content ones.
        assert any("/wiki/api/v2/spaces" in u for u in fake.urls)
        assert any("/wiki/api/v2/pages?" in u for u in fake.urls)
        assert not any("/rest/api/" in u for u in fake.urls)
        # Pages are filtered by numeric space id, not by space key.
        assert any("space-id=9001" in u for u in fake.urls)

        assert [r.ref for r in refs] == ["111", "222"]
        assert [r.path for r in refs] == ["ENG/111", "ENG/222"]
        # last_edited comes from version.createdAt, not the page createdAt.
        assert [r.last_edited for r in refs] == [
            "2026-08-01T10:00:00.000Z",
            "2026-08-05T12:30:00.000Z",
        ]
        assert refs[0].source_url == f"{BASE_URL}/wiki/spaces/ENG/pages/111"
        # v2 has no email on the page payload — see the module docstring.
        assert refs[0].sender_email == ""

    async def test_last_edited_falls_back_to_page_created_at(self) -> None:
        page = _page("111")
        del page["version"]
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9001", "key": "ENG", "name": "Engineering"}],
            pages=[page],
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(_connector())

        assert refs[0].last_edited == "2026-07-01T09:00:00.000Z"

    async def test_configured_space_keys_filter_and_skip_invisible(self) -> None:
        fake = _FakeConfluenceCloud(
            spaces=[
                {"id": "9001", "key": "ENG", "name": "Engineering"},
                {"id": "9002", "key": "OPS", "name": "Operations"},
            ],
            pages=[_page("111")],
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(
                _connector(space_keys=["OPS", "GHOST"])
            )

        # Only OPS is synced; GHOST is not visible and must not fail the run.
        assert all(r.path.startswith("OPS/") for r in refs)
        assert any("space-id=9002" in u for u in fake.urls)
        assert not any("space-id=9001" in u for u in fake.urls)

    async def test_configured_keys_use_the_v2_keys_filter(self) -> None:
        """A configured space key is resolved by filter, not by full listing.

        A tenant with more spaces than _MAX_SPACES would otherwise have its
        configured space fall off the end of an unfiltered listing and sync
        silently empty.
        """
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9002", "key": "OPS", "name": "Operations"}],
            pages=[_page("111")],
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            await ConfluenceAdapter(SimpleNamespace()).list_documents(
                _connector(space_keys=["OPS"])
            )

        space_urls = [u for u in fake.urls if "/wiki/api/v2/spaces" in u]
        assert space_urls, "no space lookup was issued"
        assert all("keys=OPS" in u for u in space_urls)

    async def test_pages_truncated_at_cap(self) -> None:
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9001", "key": "ENG", "name": "Engineering"}],
            pages=[_page(str(i)) for i in range(_MAX_PAGES_PER_SPACE + 25)],
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(_connector())

        assert len(refs) == _MAX_PAGES_PER_SPACE

    async def test_page_listing_failure_fails_the_run(self) -> None:
        """A listing that errored is not a listing.

        Swallowing it and returning the pages that happened to arrive would
        present a partial space as the whole space, and the sync engine reads
        every page it never saw as absent. The exception reaches the sync
        runner, which marks the run FAILED.
        """
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9001", "key": "ENG", "name": "Engineering"}],
            page_list_status=500,
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch, pytest.raises(requests.HTTPError):
            await ConfluenceAdapter(SimpleNamespace()).list_documents(_connector())

    async def test_pagination_follows_the_cursor_across_pages(self) -> None:
        """Pages beyond the first batch are collected, not dropped."""
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9001", "key": "ENG", "name": "Engineering"}],
            pages=[_page(str(i)) for i in range(5)],
            page_size=2,
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(_connector())

        assert [r.ref for r in refs] == ["0", "1", "2", "3", "4"]
        # 5 pages at 2 per request = 3 round-trips, and the cursor is carried.
        page_calls = [u for u in fake.urls if "/wiki/api/v2/pages?" in u]
        assert len(page_calls) == 3
        assert any("cursor=" in u for u in page_calls)

    async def test_pagination_stops_at_the_cap_instead_of_walking_the_space(
        self,
    ) -> None:
        """The cap must bound the API traffic, not just what we keep.

        Sol's review of the migration: capping the SDK's own result only
        trimmed the list after it had already fetched the entire space.
        """
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9001", "key": "ENG", "name": "Engineering"}],
            pages=[_page(str(i)) for i in range(_MAX_PAGES_PER_SPACE * 3)],
            page_size=_MAX_PAGES_PER_SPACE,
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(_connector())

        assert len(refs) == _MAX_PAGES_PER_SPACE
        # One request, then stop — not three.
        assert len([u for u in fake.urls if "/wiki/api/v2/pages?" in u]) == 1

    async def test_cursor_is_followed_in_the_href_form_too(self) -> None:
        """``_links.next`` comes as a bare string or as ``{"href": ...}``.

        ConfluenceBase._get_paged handles both. Reading only one would end a
        listing after its first page without saying so.
        """
        fake = _FakeConfluenceCloud(
            spaces=[{"id": "9001", "key": "ENG", "name": "Engineering"}],
            pages=[_page(str(i)) for i in range(5)],
            page_size=2,
            href_form_cursor=True,
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(_connector())

        assert [r.ref for r in refs] == ["0", "1", "2", "3", "4"]

    async def test_explicit_space_keys_are_not_capped_at_max_spaces(self) -> None:
        """_MAX_SPACES bounds discovery, not an operator's explicit list.

        Nothing in the portal caps space_keys. Capping the filtered listing
        would drop the spaces past the hundredth and then report them as not
        visible to the token, which misstates the reason.
        """
        keys = [f"S{i}" for i in range(_MAX_SPACES + 10)]
        fake = _FakeConfluenceCloud(
            spaces=[{"id": str(9000 + i), "key": k} for i, k in enumerate(keys)],
            pages=[_page("1")],
            page_size=50,
        )
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            refs = await ConfluenceAdapter(SimpleNamespace()).list_documents(
                _connector(space_keys=keys)
            )

        assert len({r.path.split("/")[0] for r in refs}) == len(keys)

    async def test_fetch_document_reads_v2_storage_body(self) -> None:
        fake = _FakeConfluenceCloud(
            storage_body="<p>Hallo <strong>wereld</strong></p>",
        )
        ref = SimpleNamespace(ref="12345", images=[])
        session_patch, dns_patch = self._patches(fake)
        with session_patch, dns_patch:
            body = await ConfluenceAdapter(SimpleNamespace()).fetch_document(
                ref, _connector()
            )

        assert any("/wiki/api/v2/pages/12345" in u for u in fake.urls)
        assert any("body-format=storage" in u for u in fake.urls)
        # The v1 expand parameter must be gone — v2 rejects it.
        assert not any("expand=body.storage" in u for u in fake.urls)
        assert "Hallo" in body.decode("utf-8")
        assert "wereld" in body.decode("utf-8")
