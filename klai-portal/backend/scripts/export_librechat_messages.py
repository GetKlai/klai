"""Read-only LibreChat message export for operators: one JSON object per message on stdout.

    uv run python -m scripts.export_librechat_messages --database DB --since <iso> --until <iso> > out.jsonl

PRIVATE CUSTOMER DATA — every line is real customer conversation content with its
error/unfinished flags. The CLI never writes a file: redirect stdout to a path
OUTSIDE this public repository. Credentials come from `settings` via the judge
module's `_mongo_client`; failed/unfinished messages stay in (quality denominators).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import pymongo

from app.services.librechat_chat_context import _iso
from app.services.librechat_quality_judge import _message_text, _mongo_client

_FIELDS = "messageId conversationId parentMessageId isCreatedByUser text content createdAt unfinished error feedback"
PROJECTION: dict[str, int] = dict.fromkeys(_FIELDS.split(), 1) | {"_id": 0}


def _parse_window(since_raw: str, until_raw: str) -> tuple[datetime, datetime]:
    window = []
    for flag, raw in (("--since", since_raw), ("--until", until_raw)):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{flag} is not an ISO 8601 timestamp: {raw!r}") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"{flag} must carry a timezone offset, e.g. +02:00 or Z: {raw!r}")
        window.append(parsed)
    since, until = window
    if since >= until:
        raise ValueError(f"--since {since_raw} must be strictly before --until {until_raw}")
    return since, until


def _json_default(value: object) -> str:
    return _iso(value) or str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", required=True, help="LibreChat Mongo database of one tenant")
    parser.add_argument("--since", required=True, help="ISO 8601 timestamp with timezone (inclusive)")
    parser.add_argument("--until", required=True, help="ISO 8601 timestamp with timezone (exclusive)")
    args = parser.parse_args(argv)
    try:
        since, until = _parse_window(args.since, args.until)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    query = {"createdAt": {"$gte": since, "$lt": until}}
    sort = [("createdAt", pymongo.ASCENDING), ("messageId", pymongo.ASCENDING)]
    with _mongo_client() as client:
        for doc in client[args.database].messages.find(query, PROJECTION).sort(sort):
            doc["normalized_text"] = _message_text(doc)
            print(json.dumps(doc, default=_json_default, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
