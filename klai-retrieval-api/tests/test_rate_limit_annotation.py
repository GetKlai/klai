"""SPEC-SEC-HYGIENE-001 REQ-42 / AC-42: rate-limit fail-open annotation.

Two parts:

* REQ-42.1 — the ``except Exception`` block in
  ``services/rate_limit.py`` MUST carry an ``@MX:WARN`` annotation with
  an ``@MX:REASON`` linking to either SPEC-SEC-HYGIENE-001 REQ-42 or the
  follow-up SPEC-RETRIEVAL-RL-FAILCLOSED-001. The annotation documents
  why we deliberately fail-open on Redis errors so the next audit doesn't
  re-file the finding.
* REQ-42.2 — the warning log emitted on the fail-open branch MUST
  capture the traceback (via ``logger.exception(...)`` or
  ``logger.warning(..., exc_info=True)``), NOT discard it via
  ``error=str(exc)``.

A behavioural sub-test confirms the fail-open contract: a Redis pool that
raises on every operation MUST cause ``check_and_increment`` to return
``(True, 0)``.

Note on the SPEC text vs. reality: the SPEC (drafted 2026-04-24) refers
to a function ``check_limit`` and a log event ``rate_limit_redis_unavailable``.
The actual function is ``check_and_increment`` and the event is
``rate_limiter_degraded``; ``logger.exception`` is already in place
(REQ-42.2 effectively pre-satisfied). This test covers what the SPEC
*intends* against the code as it stands today.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RL = _REPO_ROOT / "retrieval_api" / "services" / "rate_limit.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# REQ-42.1 — MX:WARN + MX:REASON on the fail-open branch
# --------------------------------------------------------------------------- #


def test_rate_limit_fail_open_carries_mx_warn_annotation():
    """REQ-42.1: ``@MX:WARN`` precedes the fail-open ``except Exception`` block."""
    src = _read(_RL)

    # Find the fail-open except block (the one returning True, 0).
    fail_open_block = re.search(
        r"except\s+Exception:[^\n]*\n"  # except line
        r"(?:[ \t]+[^\n]*\n)+"           # body lines
        r"[ \t]+return\s+True,\s*0",     # ending in `return True, 0`
        src,
    )
    assert fail_open_block, (
        "Could not locate the fail-open `except Exception:` block in "
        "rate_limit.py — has the function shape changed?"
    )

    # Look for MX:WARN within ~12 lines before the except.
    block_start = fail_open_block.start()
    pre_block = src[max(0, block_start - 800) : block_start]
    assert "@MX:WARN" in pre_block, (
        "Fail-open `except Exception:` block lacks an `@MX:WARN` annotation "
        "in the preceding ~12 lines. REQ-42.1 requires the annotation so "
        "the next audit sees this is a deliberate availability choice."
    )
    assert "@MX:REASON" in pre_block, (
        "Fail-open `except Exception:` block lacks an `@MX:REASON` "
        "annotation. REQ-42.1 requires the rationale to be machine-readable."
    )

    # Reason must reference either this SPEC or the future fail-closed SPEC.
    assert re.search(
        r"SPEC-SEC-HYGIENE-001\s+REQ-42|SPEC-RETRIEVAL-RL-FAILCLOSED-001",
        pre_block,
    ), (
        "@MX:REASON does not reference SPEC-SEC-HYGIENE-001 REQ-42 or "
        "SPEC-RETRIEVAL-RL-FAILCLOSED-001 — needed for traceability."
    )


# --------------------------------------------------------------------------- #
# REQ-42.2 — fail-open branch logs the traceback (no `error=str(exc)`)
# --------------------------------------------------------------------------- #


def test_rate_limit_fail_open_logs_with_traceback():
    """REQ-42.2: the warning log captures the traceback."""
    src = _read(_RL)
    # Strip line comments so explanatory text doesn't trip the guard.
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert "error=str(exc)" not in code, (
        "rate_limit.py still logs via `error=str(exc)` (TRY401) — the "
        "traceback would be discarded. Use logger.exception(...) or "
        "logger.warning(..., exc_info=True)."
    )
    # Confirm the redis_unreachable log path uses one of the two safe forms.
    assert re.search(
        r"logger\.exception\([^)]*redis_unreachable|"
        r"logger\.warning\([^)]*redis_unreachable[^)]*exc_info\s*=\s*True",
        src,
    ), (
        "Fail-open log call for `redis_unreachable` must use "
        "logger.exception(...) or logger.warning(..., exc_info=True)."
    )


# --------------------------------------------------------------------------- #
# AC-42 step 6/7 — behavioural fail-open contract
# --------------------------------------------------------------------------- #


async def test_rate_limit_fails_open_when_redis_pipeline_raises(monkeypatch):
    """REQ-42 (behavioural): Redis errors → ``(True, 0)`` (allow + no retry)."""
    from retrieval_api.services import rate_limit

    class _RaisingPipeline:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        def zremrangebyscore(self, *_a, **_kw):
            return None

        def zcard(self, *_a, **_kw):
            return None

        def zadd(self, *_a, **_kw):
            return None

        def expire(self, *_a, **_kw):
            return None

        async def execute(self):
            raise ConnectionError("simulated redis outage")

    class _RaisingPool:
        def pipeline(self, transaction: bool = True):  # noqa: ARG002
            return _RaisingPipeline()

    async def _fake_pool(_url: str):
        return _RaisingPool()

    monkeypatch.setattr(rate_limit, "get_redis_pool", _fake_pool)

    allowed, retry_after = await rate_limit.check_and_increment(
        redis_url="redis://does-not-matter",
        key="test:key",
        limit_per_minute=10,
    )
    assert allowed is True, "fail-open contract broken — Redis error must allow request"
    assert retry_after == 0, "fail-open path must not surface a Retry-After"
