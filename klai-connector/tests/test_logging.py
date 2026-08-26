"""Structured logging regression tests."""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog

from app.core.logging import get_logger, setup_logging


def test_stdlib_extra_fields_are_serialized_as_top_level_json(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setattr("app.core.logging.sys.stdout", stream)

    try:
        setup_logging(service_name="klai-connector-test")
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id="request-1", org_id="org-1")
        get_logger("test.sync").info(
            "Sync complete for connector %s",
            "connector-1",
            extra={
                "event": "sync_complete",
                "groups_total": 2,
                "records_total": 3,
                "duplicates_collapsed": 1,
                "stale_groups_deleted": 4,
                "documents_total": 5,
            },
        )
    finally:
        root_logger.handlers.clear()
        root_logger.handlers.extend(previous_handlers)
        root_logger.setLevel(previous_level)
        structlog.contextvars.clear_contextvars()

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "sync_complete"
    assert payload["groups_total"] == 2
    assert payload["records_total"] == 3
    assert payload["duplicates_collapsed"] == 1
    assert payload["stale_groups_deleted"] == 4
    assert payload["documents_total"] == 5
    assert payload["request_id"] == "request-1"
    assert payload["org_id"] == "org-1"
