"""Export CLI: private rows out, identity fields and bad windows never reach Mongo."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from scripts import export_librechat_messages as export

_BASE = ["--database", "sample-chat", "--until", "2026-09-01T00:00:00+00:00"]


def _msg(mid: str, hour: int, **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {key: None for key in export.PROJECTION if key != "_id"}
    doc.update(messageId=mid, isCreatedByUser=True, createdAt=datetime(2026, 8, 1, hour))
    return doc | extra


def test_export_writes_normalized_private_rows_without_identity_fields(monkeypatch, capsys):
    parts = [{"type": "text", "text": "Factuur 12 staat klaar."}, {"type": "error", "error": "timeout"}]
    docs = [
        _msg("m-1", 11, text="Wat is mijn factuur?"),
        _msg("m-2", 12, isCreatedByUser=False, parentMessageId="m-1", error=True, unfinished=True, content=parts),
    ]
    client = MagicMock()
    client.__enter__.return_value = client
    client.__getitem__.return_value = client
    client.messages.find.return_value.sort.return_value = docs
    monkeypatch.setattr(export, "_mongo_client", lambda: client)
    code = export.main([*_BASE, "--since", "2026-08-01T00:00:00+00:00"])
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 0 and client.__getitem__.call_args.args == ("sample-chat",)
    query, projection = client.messages.find.call_args.args
    assert query == {"createdAt": {"$gte": datetime(2026, 8, 1, tzinfo=UTC), "$lt": datetime(2026, 9, 1, tzinfo=UTC)}}
    assert "user" not in projection and "sender" not in projection and projection["_id"] == 0
    assert client.messages.find.return_value.sort.call_args.args[0] == [("createdAt", 1), ("messageId", 1)]
    assert [row["messageId"] for row in rows] == ["m-1", "m-2"]
    assert rows[1]["normalized_text"] == "Factuur 12 staat klaar."
    assert rows[1]["error"] is True and rows[1]["unfinished"] is True and rows[1]["parentMessageId"] == "m-1"
    assert rows[1]["createdAt"] == "2026-08-01T12:00:00+00:00"
    assert all(set(row) <= set(projection) | {"normalized_text"} for row in rows)


def test_bad_window_is_refused_before_any_mongo_call(monkeypatch, capsys):
    client = MagicMock()
    monkeypatch.setattr(export, "_mongo_client", lambda: client)
    for since in ("gisteren", "2026-08-01T00:00:00", "2026-09-01T00:00:00+00:00"):
        assert export.main([*_BASE, "--since", since]) == 2
    client.__getitem__.assert_not_called()
    assert capsys.readouterr().err.count("error:") == 3
