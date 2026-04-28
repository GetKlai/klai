"""klai-connector wrapper around klai-log-utils sanitize_response_body.

Binds the connector ``Settings`` instance so call sites do not need to
thread it through every log / persist statement.

SPEC-SEC-INTERNAL-001 REQ-4 + REQ-10.
"""

from __future__ import annotations

from log_utils import extract_secret_values
from log_utils import sanitize_response_body as _sanitize

from app.core.config import Settings


def sanitize_response_body(
    settings: Settings,
    exc_or_response: object,
    *,
    max_len: int = 512,
) -> str:
    """Return a body string safe to log or persist, with connector secrets scrubbed.

    Drop-in replacement for ``exc.response.text`` / ``resp.text[:N]`` in
    log statements AND for the ``error_details`` JSONB write path. The
    connector's Settings instance is the source of truth for the secret
    set; rebuilding on every call costs <50 us and keeps tests that
    monkey-patch a settings field honest.
    """
    return _sanitize(exc_or_response, extract_secret_values(settings), max_len=max_len)


__all__ = ["sanitize_response_body"]
