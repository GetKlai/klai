# Webhook Signature Timing Benchmarks

## What these tests are for

These benchmarks guard against **constant-time regression** on webhook signature
verification. They are NOT performance tests.

The webhook endpoints harden against timing oracles using `hmac.compare_digest`.
A timing oracle occurs when a comparison leaks how many leading bytes of the
expected value match the attacker-controlled input — allowing a byte-by-byte
brute-force attack. Python's `==` operator is variable-time: it short-circuits
on the first differing byte, which leaks information. `hmac.compare_digest`
runs in constant time regardless of where the first difference occurs.

**Covered endpoints:**

| File | Endpoint | Secret field |
|------|----------|-------------|
| `test_moneybird_webhook_timing.py` | `POST /api/webhooks/moneybird` | `MONEYBIRD_WEBHOOK_TOKEN` (token in JSON body) |
| `test_vexa_webhook_timing.py` | `POST /api/bots/internal/webhook` | `VEXA_WEBHOOK_SECRET` (Bearer + Basic auth) |

## How the regression detector works

Each test runs three cohorts of 1000 comparisons:

- **CORRECT**: the supplied token exactly matches the expected secret
- **MISMATCH_FIRST_BYTE**: the token differs at byte 0 from the expected secret
- **MISMATCH_LAST_BYTE**: the token differs at the final byte only

If `hmac.compare_digest` is ever accidentally replaced with `==`:

- A correct token would be fast (no mismatch found, returns True after full scan)
- A first-byte mismatch would be very fast (returns False immediately)
- A last-byte mismatch would be slow (iterates through all bytes before returning False)

This produces a detectable timing difference between cohorts. The assertion checks
that **P95 latency between any two cohorts differs by less than 10%** of the
smallest P95. A variable-time `==` typically produces 5-20x difference between
first-byte and last-byte mismatch cohorts.

## Running the benchmarks

```bash
cd klai-portal/backend

# Run all webhook timing benchmarks
uv run pytest -m benchmark tests/benchmarks/ -v -s

# Run a specific endpoint
uv run pytest -m benchmark tests/benchmarks/test_moneybird_webhook_timing.py -v -s
uv run pytest -m benchmark tests/benchmarks/test_vexa_webhook_timing.py -v -s
```

The `-s` flag prints the observed P95 and median latencies for each cohort.

**Important:** Do NOT run with `--cov` (coverage). Coverage instrumentation adds
uniform overhead to every comparison call, which happens to partially mask
variable-time behaviour. Run timing benchmarks clean.

## Why these tests are not in the default test run

Timing tests are inherently noisy on shared CI runners (CPU contention, scheduler
preemption, background I/O). A 10% threshold that passes on a developer's machine
may intermittently fail on a loaded CI runner. Marking them `@pytest.mark.benchmark`
and excluding them from the default run (via `addopts = "-m 'not benchmark'"` in
`pyproject.toml`) prevents false CI failures while keeping the tests runnable
locally and in a dedicated timing-focused job.

To wire these into CI, create a separate job that runs on a dedicated runner with
CPU pinning and runs `pytest -m benchmark`.

## Do not remove these tests

Future refactors may be tempted to "simplify" the comparisons back to `==` for
readability. These tests exist precisely to catch that. Before removing any
benchmark test, verify that the production code still uses `hmac.compare_digest`
for all comparison paths listed in the table above.

## Tolerance band rationale

The ±2x tolerance band on the recorded baseline (relative inter-cohort P95
variance < 10%) is chosen to:

- Pass through normal scheduling jitter (±5-8% is typical on modern hardware)
- Catch a real `==` regression (which typically shows ≥200% difference between
  first-byte and last-byte mismatch cohorts on a 32-byte secret)
- Survive a future faster machine (the tolerance is relative, not absolute)
