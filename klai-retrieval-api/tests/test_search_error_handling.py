"""SPEC-SEC-HYGIENE-001 REQ-43 / AC-43: TRY-rule antipattern fixes in search.py.

Three sub-checks:

* REQ-43.1 — ``except (TimeoutError, Exception)`` is dead code (TimeoutError
  is a subclass of Exception). Use ``except Exception`` instead, with a
  separate ``except TimeoutError`` branch placed before it when timeouts
  need distinct handling.
* REQ-43.2 — ``logger.error("...", error=str(exc))`` (and the same pattern
  at warning level) discards the traceback. Use ``logger.exception(...)``
  inside an ``except`` block, or ``logger.warning(..., exc_info=True)`` if
  the failure is expected and warning-level is appropriate.
* REQ-43.3 — every other file in retrieval-api gets the same treatment:
  no ``error=str(exc)`` survives anywhere under ``retrieval_api/``.
* REQ-43.4 — ``ruff check retrieval_api/services/search.py`` exits 0 with
  the project's lint config (which now includes the ``TRY`` rule set).
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SEARCH_PY = _REPO_ROOT / "retrieval_api" / "services" / "search.py"
_RETRIEVAL_API_PKG = _REPO_ROOT / "retrieval_api"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Return source with ``# ...`` end-of-line comments removed.

    The HYGIENE-001 fix comments deliberately quote the old antipattern
    string for traceability (``# ... the previous error=str(exc) ...``);
    those mentions inside comments must NOT trip the grep guards.
    """
    lines: list[str] = []
    for line in text.splitlines():
        # Naive but safe enough: split on the first '#' that isn't inside
        # a string literal. For our static guard, false positives in test
        # data strings are acceptable since they wouldn't match the exact
        # pattern we're guarding against anyway.
        idx = line.find("#")
        if idx >= 0:
            lines.append(line[:idx])
        else:
            lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# REQ-43.1 — no `except (TimeoutError, Exception)` in search.py
# --------------------------------------------------------------------------- #


def test_search_does_not_use_timeouterror_in_exception_tuple():
    """REQ-43.1: ``except (TimeoutError, Exception)`` is dead code."""
    src = _read(_SEARCH_PY)
    bad = re.findall(r"except\s*\(\s*TimeoutError\s*,\s*Exception\s*\)", src)
    assert not bad, (
        "search.py still contains `except (TimeoutError, Exception)` — "
        "TimeoutError is a subclass of Exception, so the TimeoutError "
        "alternative is unreachable. Use `except Exception` (or split "
        "into two branches with TimeoutError first)."
    )

    # Also catch the reverse ordering for safety.
    reverse = re.findall(r"except\s*\(\s*Exception\s*,\s*TimeoutError\s*\)", src)
    assert not reverse, "search.py still contains `except (Exception, TimeoutError)`."


# --------------------------------------------------------------------------- #
# REQ-43.2 — no `error=str(exc)` in search.py
# --------------------------------------------------------------------------- #


def test_search_does_not_log_via_error_str_exc():
    """REQ-43.2: ``error=str(exc)`` discards the traceback (TRY401)."""
    src = _strip_comments(_read(_SEARCH_PY))
    assert "error=str(exc)" not in src, (
        "search.py still logs via `error=str(exc)` — use "
        "`logger.exception(...)` (preserves traceback) or "
        "`logger.warning(..., exc_info=True)` instead."
    )


# --------------------------------------------------------------------------- #
# REQ-43.3 — same fix applied across the rest of retrieval-api
# --------------------------------------------------------------------------- #


def test_no_error_str_exc_anywhere_under_retrieval_api():
    """REQ-43.3: grep `retrieval_api/` for the same pattern; uniformly fixed."""
    offenders: list[str] = []
    for py in _RETRIEVAL_API_PKG.rglob("*.py"):
        if "error=str(exc)" in _strip_comments(_read(py)):
            offenders.append(str(py.relative_to(_REPO_ROOT)))

    assert not offenders, (
        "Files still using `error=str(exc)` (drops traceback): "
        + ", ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# REQ-43.2 (behavioural) — exception with traceback reaches the log handler
# --------------------------------------------------------------------------- #


def _capture_records():
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    root = logging.getLogger()
    prev_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    return records, handler, prev_level


@pytest.fixture
def log_capture():
    # Ensure ``setup_logging`` (and therefore ``structlog.configure``) has
    # run BEFORE the structlog loggers in retrieval_api.services.* are
    # first-used in this test. ``cache_logger_on_first_use=True`` would
    # otherwise pin them to structlog's default PrintLogger(stderr), and
    # our root-handler capture would never see the records.
    from retrieval_api.logging_setup import setup_logging

    setup_logging()
    records, handler, prev_level = _capture_records()
    try:
        yield records
    finally:
        logging.getLogger().removeHandler(handler)
        logging.getLogger().setLevel(prev_level)


async def test_search_logs_exception_with_traceback_on_qdrant_failure(monkeypatch, log_capture):
    """REQ-43.2 (behavioural): a TimeoutError surfaces as a log record with traceback.

    Strategy: stub ``_get_client`` so ``query_points`` raises ``TimeoutError``
    inside the existing ``asyncio.wait_for`` await. The exception bubbles
    through the ``except`` block — which must log via ``logger.exception(...)``
    so ``rec.exc_info`` is populated on the captured ``LogRecord``.
    """
    from retrieval_api.services import search

    class _RaisingClient:
        async def query_points(self, *_a, **_kw):
            raise TimeoutError("simulated qdrant timeout")

    monkeypatch.setattr(
        "retrieval_api.services.search._get_client",
        lambda: _RaisingClient(),
    )

    class _Req:
        # Minimal duck-typed request — `_search_notebook` only reads these.
        notebook_id = "nb-1"
        org_id = "org-xyz"

    with pytest.raises(TimeoutError):
        await search._search_notebook([0.1] * 8, _Req(), candidates=10)

    # structlog's ``wrap_for_formatter`` processor passes the post-processed
    # event_dict as ``record.msg`` (a dict, not a string). The
    # ``format_exc_info`` processor — configured in
    # ``logging_setup.setup_logging`` — has already rendered the traceback
    # into ``event_dict["exception"]`` before this point, so the stdlib
    # ``record.exc_info`` is no longer carrying the traceback either.
    def _event_dict(rec: logging.LogRecord) -> dict:
        if isinstance(rec.msg, dict):
            return rec.msg
        return {}

    qdrant_records = [
        rec for rec in log_capture
        if _event_dict(rec).get("event") == "qdrant_search_failed"
    ]
    assert qdrant_records, (
        "Expected `qdrant_search_failed` log record, found none. "
        f"All captured event names: {[_event_dict(r).get('event') for r in log_capture]}"
    )

    has_traceback = any(
        "TimeoutError" in str(_event_dict(rec).get("exception", ""))
        for rec in qdrant_records
    )
    assert has_traceback, (
        "qdrant_search_failed log record has no rendered traceback in the "
        "structlog event dict — `logger.exception(...)` either was not used "
        "or the `format_exc_info` processor is no longer wired in. "
        "REQ-43.2 expects the traceback to survive the log call."
    )


# --------------------------------------------------------------------------- #
# REQ-43.4 — ruff TRY rules pass on search.py
# --------------------------------------------------------------------------- #


def test_ruff_try_rules_pass_on_search_py():
    """REQ-43.4: ruff ``TRY`` rules are clean on search.py.

    Scoped via ``--select TRY`` to a) verify the antipattern (TRY400 on
    ``logger.error`` inside ``except``) is gone and b) keep this assertion
    independent of pre-existing non-TRY lint issues that are out of
    HYGIENE-001 REQ-43 scope (e.g. E402 caused by the
    ``warnings.filterwarnings`` call positioned before late imports).
    """
    result = subprocess.run(  # noqa: S603 — invocation args are controlled within this test
        [sys.executable, "-m", "ruff", "check", "--select", "TRY", str(_SEARCH_PY)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"ruff TRY check failed for search.py:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def test_ruff_pyproject_enforces_try_rules():
    """REQ-43.4: ``pyproject.toml`` lint config enables the ``TRY`` rule set.

    This is the CI-side guard: even if ``search.py`` is clean today, a
    future regression that re-introduces ``logger.error(...)`` inside
    ``except`` should fail CI on the next PR.
    """
    pyproject = _read(_REPO_ROOT / "pyproject.toml")
    # Look for "TRY" inside the lint.select list (allow whitespace, quotes).
    select_match = re.search(
        r"\[tool\.ruff\.lint\][^[]*?select\s*=\s*\[([^\]]*)\]",
        pyproject,
        flags=re.DOTALL,
    )
    assert select_match, "Could not locate `[tool.ruff.lint] select = [...]` in pyproject.toml"
    select_body = select_match.group(1)
    assert re.search(r"['\"]TRY['\"]", select_body), (
        "pyproject.toml `[tool.ruff.lint] select` does not enable TRY rules. "
        "Required by REQ-43.4 to prevent regression."
    )
