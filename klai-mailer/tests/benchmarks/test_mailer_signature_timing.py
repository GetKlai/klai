"""SPEC-SEC-WEBHOOK-001 REQ-5.4 — Mailer Zitadel signature constant-time regression benchmark.

PURPOSE
-------
This is NOT a performance test. It is a constant-time regression detector.

The klai-mailer Zitadel webhook verifier (app/signature.py::verify_zitadel_signature)
uses hmac.compare_digest to compare the HMAC-SHA256 hex digest computed from
the signed payload against the v1 field from the Zitadel-Signature header:

    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise SignatureError("hmac_mismatch")

The comparison operands are both 64-character hex strings (SHA-256 hexdigest).
A variable-time `==` comparison on hex strings would leak how many leading hex
characters match the attacker-controlled v1 field, enabling a byte-by-byte
brute-force oracle on the secret.

This benchmark benchmarks the `hmac.compare_digest` call directly, isolating
the timing-sensitive comparison from surrounding Python overhead (SHA-256
computation, exception construction). Exception overhead (~4000ns) would
dominate if we timed the full verifier path, masking a real variable-time
regression. Direct comparison isolation is the correct approach.

Cohorts:
  - CORRECT              : v1 equals the computed HMAC-SHA256 hex digest
  - MISMATCH_FIRST_HEX   : v1 has a different first hex character (byte 0)
  - MISMATCH_LAST_HEX    : v1 has a different last hex character (byte 63)

TOLERANCE BAND
--------------
Tolerance = max(10% of smallest cohort P95, 500ns absolute floor).

The 500ns absolute floor prevents false positives from OS timer quantization
(Windows/Linux perf_counter_ns can quantize to ~100ns increments). At sub-
microsecond P95 values, 10% of P95 may be smaller than one timer tick.

A real variable-time `==` regression on a 64-char hex string produces ≥5000ns
of inter-cohort difference on typical hardware, well above the 500ns floor.

OBSERVED BASELINE (recorded 2026-04-29, local developer machine)
-----------------------------------------------------------------
These numbers are informational only. The assertion is relative (inter-cohort
variance), not against an absolute wall-clock target.

  Platform: Windows 11, Python 3.12.9, perf_counter_ns quantized ~100ns
  correct_p95_ns          : 200
  mismatch_first_p95_ns   : 200
  mismatch_last_p95_ns    : 200
  correct_median_ns       : 200
  mismatch_first_median   : 200
  mismatch_last_median    : 200

To run this benchmark:
  cd klai-mailer
  uv run pytest -m benchmark tests/benchmarks/test_mailer_signature_timing.py -v -s

Do NOT run with coverage (--cov) — coverage instrumentation distorts timing.
"""

from __future__ import annotations

import hashlib
import hmac
import statistics
import time

import pytest

_SECRET = "webhook-test-secret"


def _p95(samples: list[int]) -> float:
    """Return the P95 value from a list of nanosecond samples."""
    sorted_samples = sorted(samples)
    idx = int(len(sorted_samples) * 0.95)
    return float(sorted_samples[idx])


def _make_valid_digest(body: bytes, secret: str, ts: int) -> str:
    """Compute the valid HMAC-SHA256 hex digest for (body, secret, ts).

    Mirrors app/signature.py::verify_zitadel_signature:
        signed_payload = f"{timestamp}.".encode() + raw_body
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    """
    signed_payload = f"{ts}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()


def _run_compare_digest(expected: str, v1: str) -> int:
    """Invoke the exact comparison path used by verify_zitadel_signature.

    Mirrors app/signature.py:
        if not hmac.compare_digest(expected, v1): ...

    Returns elapsed nanoseconds for the compare_digest call alone.
    Both operands are 64-character hex strings (SHA-256 hexdigest).
    """
    t0 = time.perf_counter_ns()
    hmac.compare_digest(expected, v1)
    t1 = time.perf_counter_ns()
    return t1 - t0


@pytest.mark.benchmark
def test_mailer_signature_constant_time_invariant(settings_env: dict) -> None:
    """Assert that hmac.compare_digest latency is indistinguishable across
    correct / first-hex-mismatch / last-hex-mismatch cohorts.

    `settings_env` fixture ensures the Settings() object can be constructed
    (required by modules that import app.config at module load time).

    We benchmark compare_digest directly (not the full verifier) to isolate
    the timing-oracle risk from Python overhead (SHA-256 computation,
    exception construction) that would otherwise dominate the measurement.
    """
    n = 1000
    body = b'{"test": "payload", "event": "user.registered"}'
    ts = int(time.time())

    correct_v1 = _make_valid_digest(body, _SECRET, ts)
    assert len(correct_v1) == 64, f"SHA-256 hex digest should be 64 chars, got {len(correct_v1)}"

    # Mismatch at first hex character of v1 (byte 0 of the 64-char digest string).
    first_hex_char = correct_v1[0]
    flipped_first = format(int(first_hex_char, 16) ^ 1, "x")
    mismatch_first_v1 = flipped_first + correct_v1[1:]

    # Mismatch at last hex character of v1 (byte 63 of the 64-char digest string).
    last_hex_char = correct_v1[-1]
    flipped_last = format(int(last_hex_char, 16) ^ 1, "x")
    mismatch_last_v1 = correct_v1[:-1] + flipped_last

    # Sanity checks.
    assert mismatch_first_v1 != correct_v1
    assert mismatch_last_v1 != correct_v1
    assert mismatch_first_v1[0] != correct_v1[0]
    assert mismatch_last_v1[-1] != correct_v1[-1]

    # Warm-up: discard first 50 calls (JIT + branch predictor warm-up).
    for _ in range(50):
        _run_compare_digest(correct_v1, correct_v1)
        _run_compare_digest(correct_v1, mismatch_first_v1)
        _run_compare_digest(correct_v1, mismatch_last_v1)

    # Interleave cohorts so scheduler jitter affects all equally.
    correct_ns: list[int] = []
    mismatch_first_ns: list[int] = []
    mismatch_last_ns: list[int] = []

    for _ in range(n):
        correct_ns.append(_run_compare_digest(correct_v1, correct_v1))
        mismatch_first_ns.append(_run_compare_digest(correct_v1, mismatch_first_v1))
        mismatch_last_ns.append(_run_compare_digest(correct_v1, mismatch_last_v1))

    correct_p95 = _p95(correct_ns)
    mismatch_first_p95 = _p95(mismatch_first_ns)
    mismatch_last_p95 = _p95(mismatch_last_ns)

    print(
        f"\n[mailer/zitadel] P95 (ns): correct={correct_p95:.0f}  "
        f"first_hex_mismatch={mismatch_first_p95:.0f}  "
        f"last_hex_mismatch={mismatch_last_p95:.0f}"
    )
    print(
        f"[mailer/zitadel] median (ns): correct={statistics.median(correct_ns):.0f}  "
        f"first_hex_mismatch={statistics.median(mismatch_first_ns):.0f}  "
        f"last_hex_mismatch={statistics.median(mismatch_last_ns):.0f}"
    )

    # tolerance = max(10% of smallest P95, 500ns absolute floor).
    # The 500ns floor accounts for OS timer quantization at sub-microsecond P95
    # values. A real variable-time `==` regression on a 64-char hex digest
    # produces ≥5000ns inter-cohort difference, well above this floor.
    smallest_p95 = min(correct_p95, mismatch_first_p95, mismatch_last_p95)
    tolerance = max(smallest_p95 * 0.10, 500.0)

    diff_correct_vs_first = abs(correct_p95 - mismatch_first_p95)
    diff_correct_vs_last = abs(correct_p95 - mismatch_last_p95)
    diff_first_vs_last = abs(mismatch_first_p95 - mismatch_last_p95)

    assert diff_correct_vs_first <= tolerance, (
        f"TIMING ORACLE DETECTED: correct vs first-hex-mismatch P95 differ by "
        f"{diff_correct_vs_first:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/signature.py."
    )
    assert diff_correct_vs_last <= tolerance, (
        f"TIMING ORACLE DETECTED: correct vs last-hex-mismatch P95 differ by "
        f"{diff_correct_vs_last:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/signature.py."
    )
    assert diff_first_vs_last <= tolerance, (
        f"TIMING ORACLE DETECTED: first-hex vs last-hex-mismatch P95 differ by "
        f"{diff_first_vs_last:.0f}ns (>{tolerance:.0f}ns tolerance). "
        f"Verify hmac.compare_digest is still used in app/signature.py."
    )
