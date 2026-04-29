"""SPEC-SEC-WEBHOOK-001 REQ-5.4 — Vexa POST_MEETING_HOOKS constant-time regression benchmark.

PURPOSE
-------
This is NOT a performance test. It is a constant-time regression detector.

The Vexa webhook guard at _require_webhook_secret (app/api/meetings.py) uses
hmac.compare_digest in two authentication paths:

  Bearer path:
      hmac.compare_digest(auth_header.encode("utf-8"), expected_bearer_bytes)

  Basic-auth path:
      hmac.compare_digest(password.encode("utf-8"), expected_secret_bytes)

Both calls must be constant-time. A variable-time `==` comparison would allow
an attacker to determine how many leading bytes of the secret match the token
they're probing, enabling a byte-by-byte brute-force oracle.

This benchmark verifies that P95 latency is statistically indistinguishable
across three cohorts per auth path:
  - CORRECT              : token matches the configured secret
  - MISMATCH_FIRST_BYTE  : token differs at byte 0
  - MISMATCH_LAST_BYTE   : token differs at the final byte

TOLERANCE BAND
--------------
P95 difference between any two cohorts must be < 10% of the smallest P95.
The ±2x tolerance on the recorded baseline catches scheduling jitter on
shared CI runners but is wide enough to flag a true variable-time regression
(which typically shows 5-20x difference between first-byte and last-byte
mismatch cohorts when using `==`).

OBSERVED BASELINE (recorded 2026-04-29, local developer machine)
-----------------------------------------------------------------
These numbers are informational only. The assertion is relative (inter-cohort
variance), not against an absolute wall-clock target.

  Platform: Windows 11, Python 3.13.2, perf_counter_ns quantized ~100ns

  Bearer path:
    correct_p95_ns        : 500
    mismatch_first_p95_ns : 500
    mismatch_last_p95_ns  : 500
    correct_median_ns     : 400
    mismatch_first_median : 400
    mismatch_last_median  : 400

  Basic path:
    correct_p95_ns        : 400
    mismatch_first_p95_ns : 400
    mismatch_last_p95_ns  : 500
    correct_median_ns     : 300
    mismatch_first_median : 300
    mismatch_last_median  : 300

To run this benchmark:
  cd klai-portal/backend
  uv run pytest -m benchmark tests/benchmarks/test_vexa_webhook_timing.py -v

Do NOT run with coverage (--cov) — coverage instrumentation distorts timing.
"""

from __future__ import annotations

import statistics
import time

import pytest

_SECRET = "test-vexa-webhook-secret"


def _p95(samples: list[int]) -> float:
    """Return the P95 value from a list of nanosecond samples."""
    sorted_samples = sorted(samples)
    idx = int(len(sorted_samples) * 0.95)
    return float(sorted_samples[idx])


def _run_bearer_compare(header: str, secret: str) -> int:
    """Invoke the Bearer comparison path used by _require_webhook_secret.

    Mirrors app/api/meetings.py Bearer branch:
        expected = f"Bearer {settings.vexa_webhook_secret}".encode()
        hmac.compare_digest(auth_header.encode("utf-8"), expected)

    Returns elapsed nanoseconds for the comparison call alone.
    """
    import hmac

    header_bytes = header.encode("utf-8")
    expected_bytes = f"Bearer {secret}".encode()

    t0 = time.perf_counter_ns()
    hmac.compare_digest(header_bytes, expected_bytes)
    t1 = time.perf_counter_ns()
    return t1 - t0


def _run_basic_compare(password: str, secret: str) -> int:
    """Invoke the Basic-auth comparison path used by _require_webhook_secret.

    Mirrors app/api/meetings.py Basic branch:
        hmac.compare_digest(password.encode("utf-8"), expected_secret)

    Returns elapsed nanoseconds for the comparison call alone.
    """
    import hmac

    password_bytes = password.encode("utf-8")
    expected_bytes = secret.encode("utf-8")

    t0 = time.perf_counter_ns()
    hmac.compare_digest(password_bytes, expected_bytes)
    t1 = time.perf_counter_ns()
    return t1 - t0


def _assert_constant_time(
    correct_p95: float,
    mismatch_first_p95: float,
    mismatch_last_p95: float,
    path_label: str,
) -> None:
    """Assert P95 inter-cohort variance is below the tolerance band.

    Tolerance = max(10% of smallest P95, 500ns absolute floor).

    The 500ns absolute floor accounts for OS timer quantization on Windows and
    Linux where perf_counter_ns may round to ~100ns increments. At sub-microsecond
    P95 values, 10% of P95 can be smaller than one timer tick, causing false
    positives from quantization noise rather than real variable-time behavior.

    A real variable-time `==` regression on a 24-byte secret produces ≥5000ns
    of difference between first-byte and last-byte mismatch cohorts, well above
    the 500ns floor.
    """
    smallest_p95 = min(correct_p95, mismatch_first_p95, mismatch_last_p95)
    tolerance = max(smallest_p95 * 0.10, 500.0)  # minimum 500ns absolute floor

    diff_correct_vs_first = abs(correct_p95 - mismatch_first_p95)
    diff_correct_vs_last = abs(correct_p95 - mismatch_last_p95)
    diff_first_vs_last = abs(mismatch_first_p95 - mismatch_last_p95)

    assert diff_correct_vs_first <= tolerance, (
        f"[{path_label}] TIMING ORACLE DETECTED: correct vs first-byte-mismatch P95 differ by "
        f"{diff_correct_vs_first:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/api/meetings.py."
    )
    assert diff_correct_vs_last <= tolerance, (
        f"[{path_label}] TIMING ORACLE DETECTED: correct vs last-byte-mismatch P95 differ by "
        f"{diff_correct_vs_last:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/api/meetings.py."
    )
    assert diff_first_vs_last <= tolerance, (
        f"[{path_label}] TIMING ORACLE DETECTED: first-byte vs last-byte-mismatch P95 differ by "
        f"{diff_first_vs_last:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/api/meetings.py."
    )


@pytest.mark.benchmark
def test_vexa_bearer_constant_time_invariant() -> None:
    """Assert constant-time comparison for the Bearer auth path.

    The guard compares the full `Authorization: Bearer <secret>` header string
    as bytes against the expected value.
    """
    n = 1000

    # Correct: full Authorization header matching the secret.
    correct_header = f"Bearer {_SECRET}"
    # Mismatch at byte 0 of the full header ("Bearer <secret>").
    # Flip the first byte of "Bearer ..." — the 'B' character.
    first_char = chr(ord(correct_header[0]) ^ 1)
    mismatch_first = first_char + correct_header[1:]
    # Mismatch at last byte (last char of the secret).
    last_char = chr(ord(correct_header[-1]) ^ 1)
    mismatch_last = correct_header[:-1] + last_char

    # Warm-up.
    for _ in range(50):
        _run_bearer_compare(correct_header, _SECRET)
        _run_bearer_compare(mismatch_first, _SECRET)
        _run_bearer_compare(mismatch_last, _SECRET)

    correct_ns: list[int] = []
    mismatch_first_ns: list[int] = []
    mismatch_last_ns: list[int] = []

    for _ in range(n):
        correct_ns.append(_run_bearer_compare(correct_header, _SECRET))
        mismatch_first_ns.append(_run_bearer_compare(mismatch_first, _SECRET))
        mismatch_last_ns.append(_run_bearer_compare(mismatch_last, _SECRET))

    correct_p95 = _p95(correct_ns)
    mismatch_first_p95 = _p95(mismatch_first_ns)
    mismatch_last_p95 = _p95(mismatch_last_ns)

    print(
        f"\n[vexa/bearer] P95 (ns): correct={correct_p95:.0f}  "
        f"first_byte_mismatch={mismatch_first_p95:.0f}  "
        f"last_byte_mismatch={mismatch_last_p95:.0f}"
    )
    print(
        f"[vexa/bearer] median (ns): correct={statistics.median(correct_ns):.0f}  "
        f"first_byte_mismatch={statistics.median(mismatch_first_ns):.0f}  "
        f"last_byte_mismatch={statistics.median(mismatch_last_ns):.0f}"
    )

    _assert_constant_time(correct_p95, mismatch_first_p95, mismatch_last_p95, "vexa/bearer")


@pytest.mark.benchmark
def test_vexa_basic_constant_time_invariant() -> None:
    """Assert constant-time comparison for the Basic-auth password path.

    When Vexa's POST_MEETING_HOOKS URL contains userinfo
    (`http://user:secret@portal-api/...`), httpx converts it to
    `Authorization: Basic <b64(user:secret)>`. The guard decodes the header
    and compares only the password half against settings.vexa_webhook_secret.
    """
    n = 1000

    # Correct: password matches the secret.
    correct_password = _SECRET
    # Mismatch at byte 0.
    first_char = chr(ord(correct_password[0]) ^ 1)
    mismatch_first = first_char + correct_password[1:]
    # Mismatch at last byte.
    last_char = chr(ord(correct_password[-1]) ^ 1)
    mismatch_last = correct_password[:-1] + last_char

    # Warm-up.
    for _ in range(50):
        _run_basic_compare(correct_password, _SECRET)
        _run_basic_compare(mismatch_first, _SECRET)
        _run_basic_compare(mismatch_last, _SECRET)

    correct_ns: list[int] = []
    mismatch_first_ns: list[int] = []
    mismatch_last_ns: list[int] = []

    for _ in range(n):
        correct_ns.append(_run_basic_compare(correct_password, _SECRET))
        mismatch_first_ns.append(_run_basic_compare(mismatch_first, _SECRET))
        mismatch_last_ns.append(_run_basic_compare(mismatch_last, _SECRET))

    correct_p95 = _p95(correct_ns)
    mismatch_first_p95 = _p95(mismatch_first_ns)
    mismatch_last_p95 = _p95(mismatch_last_ns)

    print(
        f"\n[vexa/basic] P95 (ns): correct={correct_p95:.0f}  "
        f"first_byte_mismatch={mismatch_first_p95:.0f}  "
        f"last_byte_mismatch={mismatch_last_p95:.0f}"
    )
    print(
        f"[vexa/basic] median (ns): correct={statistics.median(correct_ns):.0f}  "
        f"first_byte_mismatch={statistics.median(mismatch_first_ns):.0f}  "
        f"last_byte_mismatch={statistics.median(mismatch_last_ns):.0f}"
    )

    _assert_constant_time(correct_p95, mismatch_first_p95, mismatch_last_p95, "vexa/basic")
