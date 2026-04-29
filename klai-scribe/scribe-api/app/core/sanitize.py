"""scribe-api wrapper around klai-log-utils sanitize_response_body.

Binds the scribe-api ``settings`` singleton so call sites do not need to
thread it through every log statement.

SPEC-SEC-INTERNAL-001 REQ-4.
"""

from __future__ import annotations

from log_utils import extract_secret_values
from log_utils import sanitize_response_body as _sanitize

from app.core.config import settings


def sanitize_response_body(exc_or_response: object, *, max_len: int = 512) -> str:
    """Return a body string safe to log, with scribe-api secrets scrubbed.

    Drop-in replacement for ``resp.text[:N]`` in log statements.
    """
    return _sanitize(exc_or_response, extract_secret_values(settings), max_len=max_len)


__all__ = ["sanitize_response_body"]
