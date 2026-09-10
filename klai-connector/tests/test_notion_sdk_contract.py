"""Notion adapter contract against the real notion-client SDK (transport faked).

The 2.x -> 3.x bump moved the SDK's own default Notion-Version to 2025-09-03, where a database row
reports parent.type "data_source_id"; the adapter's old filter matched on that type, so every page
was dropped and the sync completed at zero documents. tests/adapters/test_notion.py fabricates its
client, so only a real Client can see the next bump. Nothing here patches the SDK, no network runs.
"""

from importlib.metadata import version

import httpx
from notion_client import Client
from notion_sync.client import RateLimitedNotionClient

from app.adapters.notion import NotionAdapter

# API version the _search_all_pages parent filter is verified against; the SDK build is read at runtime.
ASSUMED_VERSION = "2025-09-03"


def _sdk_default() -> str:
    return str(Client(auth="secret_contract_test", client=httpx.Client()).client.headers["Notion-Version"])


def _drift(observed: str) -> str:
    return (
        f"notion-client {version('notion-client')} now defaults to Notion-Version {observed!r}, but the adapter's "
        f"parent filter is verified against {ASSUMED_VERSION!r}. Each API version can move the database-row parent "
        "shape (2025-09-03 renamed parent.type database_id to data_source_id): re-verify "
        "app/adapters/notion.py::_search_all_pages, then update ASSUMED_VERSION."
    )


def test_sdk_default_notion_version_is_what_the_adapter_assumes() -> None:
    assert (observed := _sdk_default()) == ASSUMED_VERSION, _drift(observed)


def test_adapter_inherits_the_sdk_default() -> None:
    built = str(NotionAdapter._build_sync_client("secret_contract_test").notion.client.headers["Notion-Version"])
    assert built == _sdk_default(), f"adapter sends {built!r}, SDK default is {_sdk_default()!r}"


def test_2025_09_03_row_passes_the_database_filter_through_the_real_sdk() -> None:
    row = {"object": "page", "parent": {"type": "data_source_id", "data_source_id": "ds1", "database_id": "db1"}}
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200 if (request.method, request.url.path) == ("POST", "/v1/search") else 404,
        json={"results": [row], "has_more": False, "next_cursor": None}))
    notion = Client(auth="secret_contract_test", client=httpx.Client(transport=transport))
    pages = NotionAdapter._search_all_pages(RateLimitedNotionClient(notion), 500, ["db1"])
    assert pages == [row], _drift(str(notion.client.headers["Notion-Version"]))
