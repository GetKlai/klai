"""Tests for ``app.logging_setup`` — the access-log filter that keeps
healthcheck spam out of ``docker logs`` while preserving signal on
real requests.

Yesterday's 2026-04-29 mailer /notify 500 outage was four-times longer to
diagnose than necessary because ``uvicorn.access`` had been suppressed
at WARNING level — request lines never appeared in ``docker logs``.
This module re-enables INFO and adds a healthcheck filter; the tests
below pin both the access-record-passes contract and the
healthcheck-record-drops contract.
"""

from __future__ import annotations

import logging

from app.logging_setup import _HealthCheckAccessFilter


def _make_access_record(method: str, path: str, status: int) -> logging.LogRecord:
    """Build a LogRecord shaped like uvicorn's access logger emits.

    uvicorn formats access lines as
    ``%s - "%s %s HTTP/%s" %d`` with ``args`` being
    ``(client_addr, method, full_path, http_version, status_code)``.
    The filter inspects ``args`` directly, so the message-format string
    here is irrelevant — only ``args`` matters.
    """
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:54321", method, path, "1.1", status),
        exc_info=None,
    )
    return record


class TestHealthCheckAccessFilter:
    """The filter MUST drop /health access lines and pass everything else."""

    def setup_method(self) -> None:
        self.filter = _HealthCheckAccessFilter()

    def test_health_get_dropped(self) -> None:
        record = _make_access_record("GET", "/health", 200)
        assert self.filter.filter(record) is False

    def test_health_with_query_dropped(self) -> None:
        """Docker healthcheck might add `?check=1` or similar — still dropped."""
        record = _make_access_record("GET", "/health?probe=1", 200)
        assert self.filter.filter(record) is False

    def test_notify_passes(self) -> None:
        """The signal record we MUST keep — every /notify request."""
        record = _make_access_record("POST", "/notify", 500)
        assert self.filter.filter(record) is True

    def test_internal_send_passes(self) -> None:
        record = _make_access_record("POST", "/internal/send", 200)
        assert self.filter.filter(record) is True

    def test_404_passes(self) -> None:
        """An unknown path is potential probing — keep it visible."""
        record = _make_access_record("GET", "/wp-admin", 404)
        assert self.filter.filter(record) is True

    def test_root_passes(self) -> None:
        record = _make_access_record("GET", "/", 200)
        assert self.filter.filter(record) is True

    def test_path_starting_with_health_passes(self) -> None:
        """The filter is byte-strict on ``/health``; only the exact path
        is dropped, not ``/health/foo`` or ``/healthcheck``."""
        for path in ("/healthcheck", "/health/sub", "/healthx"):
            record = _make_access_record("GET", path, 200)
            assert self.filter.filter(record) is True, f"path {path!r} was dropped"

    def test_record_with_no_args_passes(self) -> None:
        """Defensive: if the record doesn't have the expected args shape
        (e.g. a future uvicorn change), the filter passes the record
        through rather than dropping a potentially-important line."""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="some other format",
            args=None,
            exc_info=None,
        )
        assert self.filter.filter(record) is True

    def test_record_with_unexpected_args_shape_passes(self) -> None:
        """Same defensive contract — short args tuple."""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="x %s",
            args=("only-one",),
            exc_info=None,
        )
        assert self.filter.filter(record) is True

    def test_record_with_non_string_path_passes(self) -> None:
        """Defensive: if args[2] is not a string, pass through rather
        than crash on ``.split``."""
        record = logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1", "GET", 12345, "1.1", 200),
            exc_info=None,
        )
        assert self.filter.filter(record) is True
