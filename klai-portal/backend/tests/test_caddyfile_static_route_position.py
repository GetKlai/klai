"""Caddyfile lint: ``handle /static/*`` MUST precede the catch-all ``handle``.

Why this test exists
====================

Caddy evaluates ``handle`` blocks top-to-bottom; the first match wins.
The OAuth consent page references ``/static/oauth/consent.css``. If a
future refactor moves the ``handle /static/*`` block below the catch-all
``handle`` (or removes it entirely), the SPA's ``file_server`` would
respond with ``/srv/portal/index.html`` for the CSS request and the
consent page would render unstyled.

The breakage is silent — Caddy returns 200 with HTML masquerading as CSS,
the browser ignores it as ``Content-Type: text/html`` is not stylesheet,
and the page falls back to its system-default font. Hard to spot in code
review; trivial to spot here.

If this test fails, options are:
  1. Move ``handle /static/*`` above the catch-all ``handle``.
  2. Remove the catch-all if static-serving moved elsewhere AND update
     this test.

Either is fine; silent drift is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Repo-relative path. backend/tests/x.py → ../../../deploy/caddy/Caddyfile
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CADDYFILE = _REPO_ROOT / "deploy" / "caddy" / "Caddyfile"


def test_static_route_precedes_catchall() -> None:
    """``handle /static/*`` line number must be < first bare ``handle {`` line."""
    if not _CADDYFILE.exists():
        pytest.skip(f"Caddyfile not found at {_CADDYFILE}")

    text = _CADDYFILE.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find every line that opens a `handle …` block. Track the first
    # /static/* match and the first BARE `handle {` (i.e. catch-all, no
    # path matcher in front of the brace).
    static_line: int | None = None
    catchall_line: int | None = None

    static_re = re.compile(r"^\s*handle\s+/static/\*\s*\{")
    catchall_re = re.compile(r"^\s*handle\s*\{")

    for i, line in enumerate(lines, start=1):
        if static_line is None and static_re.match(line):
            static_line = i
        if catchall_line is None and catchall_re.match(line):
            catchall_line = i
        if static_line and catchall_line:
            break

    assert static_line is not None, (
        "Caddyfile no longer has a `handle /static/*` block. If portal-api "
        "static serving moved (e.g. to a CDN), update or remove this test."
    )
    assert catchall_line is not None, (
        "Caddyfile no longer has a catch-all `handle {` block — verify /static/* is still served from somewhere."
    )
    assert static_line < catchall_line, (
        f"Caddyfile order regression: `handle /static/*` (line {static_line}) "
        f"comes AFTER the catch-all `handle {{` (line {catchall_line}). "
        "Caddy evaluates handles top-to-bottom; the catch-all will swallow "
        "/static/* requests and the OAuth consent page CSS will silently "
        "fail. Move /static/* back above the catch-all."
    )
