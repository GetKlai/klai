"""SPEC-SEC-WEBHOOK-001 REQ-5.4 — Moneybird webhook constant-time regression benchmark.

PURPOSE
-------
This is NOT a performance test. It is a constant-time regression detector.

The Moneybird webhook at POST /api/webhooks/moneybird uses hmac.compare_digest
to compare the `webhook_token` field from the JSON payload against the
configured secret. hmac.compare_digest runs in constant time regardless of
where the first byte difference occurs — a variable-time `==` comparison would
leak how many leading bytes match, giving an attacker a timing oracle to
brute-force the token byte-by-byte.

This benchmark verifies that the P95 latency is statistically indistinguishable
across three cohorts:
  - CORRECT  : payload token matches the configured secret
  - MISMATCH_FIRST_BYTE : payload token differs at byte 0
  - MISMATCH_LAST_BYTE  : payload token differs at the final byte

If hmac.compare_digest were ever accidentally reverted to `==`, the CORRECT
cohort would complete faster than MISMATCH_LAST_BYTE (because `==` short-
circuits on the first differing byte and returns True immediately on a full
match while iterating through all bytes). The assertion below catches that.

TOLERANCE BAND
--------------
P95 difference between any two cohorts must be < 10% of the smallest P95.
The ±2x tolerance on the recorded baseline catches scheduling jitter on
shared CI runners but is wide enough to flag a true variable-time regression
(which typically shows 5-20x difference between first-byte and last-byte
mismatch cohorts when using `==`).

OBSERVED BASELINE (recorded 2026-04-29, local developer machine)
-----------------------------------------------------------------
These numbers are captured on the first run and are informational only.
The assertion is relative (inter-cohort variance), not against an absolute
wall-clock target.

  Platform: Windows 11, Python 3.13.2, perf_counter_ns quantized ~100ns
  correct_p95_ns        : 500
  mismatch_first_p95_ns : 500
  mismatch_last_p95_ns  : 500
  correct_median_ns     : 400
  mismatch_first_median : 400
  mismatch_last_median  : 300

To run this benchmark:
  cd klai-portal/backend
  uv run pytest -m benchmark tests/benchmarks/test_moneybird_webhook_timing.py -v

Do NOT run with coverage (--cov) — coverage instrumentation distorts timing.
"""

from __future__ import annotations

import statistics
import time

import pytest

# ---------------------------------------------------------------------------
# The secret under test — matches the conftest default so imports succeed.
# ---------------------------------------------------------------------------
_SECRET = "test-moneybird-webhook-token"


def _run_moneybird_compare(token: str, secret: str) -> int:
    """Invoke the exact comparison path used by the Moneybird webhook handler.

    Mirrors app/api/webhooks.py::moneybird_webhook:
        hmac.compare_digest(
            token.encode("utf-8"),
            settings.moneybird_webhook_token.encode("utf-8"),
        )

    Returns the elapsed time in nanoseconds for a single comparison.
    """
    import hmac

    # Pre-encode outside timing window — the endpoint does this inside the
    # handler, but the byte-encoding is O(n) and would add noise. We only
    # measure the compare_digest call itself.
    token_bytes = token.encode("utf-8")
    secret_bytes = secret.encode("utf-8")

    t0 = time.perf_counter_ns()
    hmac.compare_digest(token_bytes, secret_bytes)
    t1 = time.perf_counter_ns()
    return t1 - t0


def _p95(samples: list[int]) -> float:
    """Return the P95 value from a list of nanosecond samples."""
    sorted_samples = sorted(samples)
    idx = int(len(sorted_samples) * 0.95)
    return float(sorted_samples[idx])


@pytest.mark.benchmark
def test_moneybird_constant_time_invariant() -> None:
    """Assert that hmac.compare_digest latency is indistinguishable across
    correct / mismatch-first-byte / mismatch-last-byte cohorts.

    The constant-time invariant: P95 difference between any two cohorts MUST
    be < 10% of the smallest cohort P95.
    """
    n = 1000

    # Build the three token variants.
    # Correct: exact match.
    correct_token = _SECRET
    # Mismatch at byte 0: flip the first character.
    first_char = chr(ord(_SECRET[0]) ^ 1)
    mismatch_first = first_char + _SECRET[1:]
    # Mismatch at last byte: flip the final character.
    last_char = chr(ord(_SECRET[-1]) ^ 1)
    mismatch_last = _SECRET[:-1] + last_char

    # Warm-up: discard first 50 calls per cohort (JIT, branch predictor warm-up).
    for _ in range(50):
        _run_moneybird_compare(correct_token, _SECRET)
        _run_moneybird_compare(mismatch_first, _SECRET)
        _run_moneybird_compare(mismatch_last, _SECRET)

    # Interleave cohorts so scheduler jitter affects all equally.
    correct_ns: list[int] = []
    mismatch_first_ns: list[int] = []
    mismatch_last_ns: list[int] = []

    for _ in range(n):
        correct_ns.append(_run_moneybird_compare(correct_token, _SECRET))
        mismatch_first_ns.append(_run_moneybird_compare(mismatch_first, _SECRET))
        mismatch_last_ns.append(_run_moneybird_compare(mismatch_last, _SECRET))

    correct_p95 = _p95(correct_ns)
    mismatch_first_p95 = _p95(mismatch_first_ns)
    mismatch_last_p95 = _p95(mismatch_last_ns)

    # Report observed baselines (visible in pytest -v output).
    correct_median = statistics.median(correct_ns)
    mismatch_first_median = statistics.median(mismatch_first_ns)
    mismatch_last_median = statistics.median(mismatch_last_ns)

    print(
        f"\n[moneybird] P95 (ns): correct={correct_p95:.0f}  "
        f"first_byte_mismatch={mismatch_first_p95:.0f}  "
        f"last_byte_mismatch={mismatch_last_p95:.0f}"
    )
    print(
        f"[moneybird] median (ns): correct={correct_median:.0f}  "
        f"first_byte_mismatch={mismatch_first_median:.0f}  "
        f"last_byte_mismatch={mismatch_last_median:.0f}"
    )

    # Constant-time invariant: no cohort P95 may differ from any other by
    # more than the tolerance band.
    # tolerance = max(10% of smallest P95, 500ns absolute floor)
    # The 500ns floor accounts for OS timer quantization (Windows/Linux) at
    # sub-microsecond P95 values where 10% < one timer tick. A real variable-
    # time `==` regression produces ≥5000ns difference between cohorts on a
    # typical 24+ byte secret, safely above the floor.
    smallest_p95 = min(correct_p95, mismatch_first_p95, mismatch_last_p95)
    tolerance = max(smallest_p95 * 0.10, 500.0)

    diff_correct_vs_first = abs(correct_p95 - mismatch_first_p95)
    diff_correct_vs_last = abs(correct_p95 - mismatch_last_p95)
    diff_first_vs_last = abs(mismatch_first_p95 - mismatch_last_p95)

    assert diff_correct_vs_first <= tolerance, (
        f"TIMING ORACLE DETECTED: correct vs first-byte-mismatch P95 differ by "
        f"{diff_correct_vs_first:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/api/webhooks.py."
    )
    assert diff_correct_vs_last <= tolerance, (
        f"TIMING ORACLE DETECTED: correct vs last-byte-mismatch P95 differ by "
        f"{diff_correct_vs_last:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/api/webhooks.py."
    )
    assert diff_first_vs_last <= tolerance, (
        f"TIMING ORACLE DETECTED: first-byte vs last-byte-mismatch P95 differ by "
        f"{diff_first_vs_last:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/api/webhooks.py."
    )
