"""
Tests for SPEC-SEC-001 Fix 3: LoggingContextMiddleware binding order.

request_id must be bound BEFORE call_next (so downstream handlers see it).
org_id/user_id must be bound AFTER call_next (because auth middleware sets them
on request.state during the route handler, not before).
"""

from unittest.mock import MagicMock

import pytest
import structlog

from app.middleware.logging_context import LoggingContextMiddleware


class TestLoggingContextMiddleware:
    """Verify the logging context middleware binds variables at the correct time."""

    @pytest.mark.asyncio
    async def test_request_id_bound_before_call_next(self) -> None:
        """request_id must be in structlog context DURING request processing."""
        middleware = LoggingContextMiddleware(app=MagicMock())

        request_id_during_call_next: str | None = None

        async def mock_call_next(request: object) -> MagicMock:
            nonlocal request_id_during_call_next
            ctx = structlog.contextvars.get_contextvars()
            request_id_during_call_next = ctx.get("request_id")
            return MagicMock()

        request = MagicMock()
        request.headers = {}  # No X-Request-ID → middleware generates UUID
        # Ensure request.state has no org_id/user_id yet
        request.state = MagicMock(spec=[])

        await middleware.dispatch(request, mock_call_next)

        # request_id must have been present during call_next
        assert request_id_during_call_next is not None
        assert len(request_id_during_call_next) > 0  # UUID string

    @pytest.mark.asyncio
    async def test_org_id_user_id_bound_after_call_next(self) -> None:
        """org_id and user_id must be bound AFTER call_next completes,
        because the auth middleware sets request.state.org_id/user_id
        during the route handler (inside call_next)."""
        middleware = LoggingContextMiddleware(app=MagicMock())

        org_id_during_call_next: str | None = "NOT_SET"
        user_id_during_call_next: str | None = "NOT_SET"

        async def mock_call_next(request: object) -> MagicMock:
            nonlocal org_id_during_call_next, user_id_during_call_next
            ctx = structlog.contextvars.get_contextvars()
            org_id_during_call_next = ctx.get("org_id")
            user_id_during_call_next = ctx.get("user_id")
            # Simulate auth middleware setting state during request
            request.state.org_id = 42  # type: ignore[union-attr]
            request.state.user_id = "uid-abc"  # type: ignore[union-attr]
            return MagicMock()

        request = MagicMock()
        request.headers = {}
        request.state = MagicMock(spec=[])

        await middleware.dispatch(request, mock_call_next)

        # org_id and user_id must NOT have been bound during call_next
        assert org_id_during_call_next is None, "org_id should not be in context during call_next"
        assert user_id_during_call_next is None, "user_id should not be in context during call_next"

        # After call_next, they should be bound in structlog context
        ctx_after = structlog.contextvars.get_contextvars()
        assert ctx_after.get("org_id") == "42"
        assert ctx_after.get("user_id") == "uid-abc"

    @pytest.mark.asyncio
    async def test_missing_state_does_not_raise(self) -> None:
        """If request.state has no org_id/user_id, dispatch must not raise."""
        middleware = LoggingContextMiddleware(app=MagicMock())

        async def mock_call_next(request: object) -> MagicMock:
            return MagicMock()

        request = MagicMock()
        request.headers = {}
        # state exists but has no org_id/user_id attributes
        request.state = MagicMock(spec=[])

        # Should not raise
        result = await middleware.dispatch(request, mock_call_next)
        assert result is not None

    @pytest.mark.asyncio
    async def test_accepts_upstream_request_id(self) -> None:
        """When X-Request-ID is provided by upstream (e.g. Caddy), use it
        instead of generating a new UUID."""
        middleware = LoggingContextMiddleware(app=MagicMock())
        upstream_id = "caddy-trace-abc-123"

        request_id_during_call_next: str | None = None

        async def mock_call_next(request: object) -> MagicMock:
            nonlocal request_id_during_call_next
            ctx = structlog.contextvars.get_contextvars()
            request_id_during_call_next = ctx.get("request_id")
            return MagicMock()

        request = MagicMock()
        request.headers = {"x-request-id": upstream_id}
        request.state = MagicMock(spec=[])

        response = await middleware.dispatch(request, mock_call_next)

        assert request_id_during_call_next == upstream_id
        assert response.headers.__setitem__.call_args_list[-1] == (("X-Request-ID", upstream_id),)

    @pytest.mark.asyncio
    async def test_response_includes_request_id_header(self) -> None:
        """X-Request-ID must be set on the response for client-side correlation."""
        middleware = LoggingContextMiddleware(app=MagicMock())

        async def mock_call_next(request: object) -> MagicMock:
            return MagicMock()

        request = MagicMock()
        request.headers = {}
        request.state = MagicMock(spec=[])

        response = await middleware.dispatch(request, mock_call_next)

        # Verify X-Request-ID was set on response headers
        response.headers.__setitem__.assert_called_with("X-Request-ID", response.headers.__setitem__.call_args[0][1])
