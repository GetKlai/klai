"""Portal-api wrapper around klai-log-utils sanitize_response_body.

Binds the portal-api ``Settings`` instance so call sites do not need to
thread it through every log statement. Use as a drop-in replacement for
``exc.response.text`` / ``resp.text[:N]`` in any logger / persist call.

SPEC-SEC-INTERNAL-001 REQ-4.4.
"""

from __future__ import annotations

from log_utils import extract_secret_values
from log_utils import sanitize_response_body as _sanitize

from app.core.config import settings


def sanitize_response_body(exc_or_response: object, *, max_len: int = 512) -> str:
    """Return a body string safe to log, with portal-api secrets scrubbed.

    Drop-in replacement for ``exc.response.text`` / ``resp.text[:N]`` in
    log statements. Returns ``""`` on ``None`` / missing body.

    The set of secret values is rebuilt on every call from the
    portal-api ``Settings`` singleton. The cost is a single ``dir()``
    walk plus a handful of ``getattr`` lookups -- well under 50 us --
    and rebuilding lets test suites that monkey-patch a settings field
    see the new value without restarting the process.
    """
    return _sanitize(exc_or_response, extract_secret_values(settings), max_len=max_len)


__all__ = ["sanitize_response_body"]
