from knowledge_ingest.models import IngestRequest
from knowledge_ingest.routes.ingest import _sanitize_ingest_request


def test_sanitize_ingest_request_strips_postgres_nul_bytes() -> None:
    req = IngestRequest(
        org_id="org-1",
        kb_slug="personal-user-1",
        path="folder\x00/doc.md",
        content="hello\x00 world",
        extra={"nested": ["a\x00b"]},
        chunks=["chunk\x001"],
        source_ref="source\x00ref",
    )

    _sanitize_ingest_request(req)

    assert req.path == "folder/doc.md"
    assert req.content == "hello world"
    assert req.extra == {"nested": ["ab"]}
    assert req.chunks == ["chunk1"]
    assert req.source_ref == "sourceref"
