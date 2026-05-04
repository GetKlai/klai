# Webhook Signature Timing Benchmarks

## What these tests are for

These benchmarks guard against **constant-time regression** on webhook signature
verification. They are NOT performance tests.

The Zitadel webhook verifier (`app/signature.py::verify_zitadel_signature`) uses
`hmac.compare_digest` to compare the computed HMAC-SHA256 hex digest against the
`v1` field from the incoming `Zitadel-Signature` header. A timing oracle occurs
when a comparison leaks how many leading hex characters of the expected digest
match the attacker-controlled input — allowing a byte-by-byte brute-force attack.

Python's `==` operator is variable-time: it short-circuits on the first differing
character. `hmac.compare_digest` runs in constant time regardless of where the
first difference occurs.

**Covered endpoints:**

| File | Endpoint | Verification module |
|------|----------|---------------------|
| `test_mailer_signature_timing.py` | `POST /notify` (Zitadel webhook) | `app/signature.py::verify_zitadel_signature` |

## How the regression detector works

Each test runs three cohorts of 1000 calls to `verify_zitadel_signature()`:

- **CORRECT**: the `v1` hex digest in the header is the valid HMAC-SHA256 for the body
- **MISMATCH_FIRST_HEX**: the first hex character of `v1` is flipped (wrong from position 0)
- **MISMATCH_LAST_HEX**: only the last hex character of `v1` is flipped (wrong at position 63)

If `hmac.compare_digest` is ever accidentally replaced with `==`:

- A first-hex-character mismatch returns False almost immediately (short-circuits at char 0)
- A last-hex-character mismatch scans the full 64-char string before returning False
- A correct digest matches immediately (but takes the same time as a full scan with `compare_digest`)

This produces a detectable timing difference between cohorts. The assertion checks
that **P95 latency between any two cohorts differs by less than 10%** of the
smallest P95.

## Running the benchmarks

```bash
cd klai-mailer

# Run all mailer timing benchmarks
uv run pytest -m benchmark tests/benchmarks/ -v -s

# Run a specific endpoint
uv run pytest -m benchmark tests/benchmarks/test_mailer_signature_timing.py -v -s
```

The `-s` flag prints the observed P95 and median latencies for each cohort.

**Important:** Do NOT run with `--cov` (coverage). Coverage instrumentation adds
uniform overhead to every line, which partially masks variable-time behaviour.
Run timing benchmarks clean.

## Why these tests are not in the default test run

Timing tests are inherently noisy on shared CI runners (CPU contention, scheduler
preemption). A 10% threshold that passes on a developer's machine may intermittently
fail on a loaded runner. Marking them `@pytest.mark.benchmark` and excluding them
from the default run (via `addopts = "-m 'not benchmark'"` in `pyproject.toml`)
prevents false CI failures.

To wire these into CI, create a separate job that runs on a dedicated runner and
executes `pytest -m benchmark`.

## Do not remove these tests

Future refactors may be tempted to "simplify" the comparison in `app/signature.py`
back to `==` for readability. These tests exist precisely to catch that. Before
removing any benchmark test, verify that the production code still uses
`hmac.compare_digest` in `verify_zitadel_signature`.

## Tolerance band rationale

The ±2x tolerance band on the recorded baseline (relative inter-cohort P95
variance < 10%) is chosen to:

- Pass through normal scheduling jitter (±5-8% is typical on modern hardware)
- Catch a real `==` regression (which typically shows ≥200% difference between
  first-character and last-character mismatch cohorts on a 64-character hex string)
- Survive a future faster machine (the tolerance is relative, not absolute)

Note that the mailer benchmark exercises the full `verify_zitadel_signature()` call
including the SHA-256 HMAC computation, which dominates the total call time. This
makes the test more conservative (harder to detect a variable-time regression in
the comparison alone), but it exercises the real production code path end-to-end.
