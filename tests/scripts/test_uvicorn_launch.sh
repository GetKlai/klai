#!/bin/sh
# tests/scripts/test_uvicorn_launch.sh
#
# Shell unit tests for scripts/uvicorn-launch.sh.
#
# Verifies the env-var override path and the DNS-fallback path without
# requiring a live Docker network or a running uvicorn process.
#
# Run locally:  sh tests/scripts/test_uvicorn_launch.sh
# Run in CI:    included in any "shell scripts" quality gate.
#
# POSIX-compatible (ash / dash / bash all supported).

set -eu

SCRIPT="$(dirname "$0")/../../scripts/uvicorn-launch.sh"

PASS=0
FAIL=0

_pass() { echo "PASS: $1"; PASS=$((PASS+1)); }
_fail() { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

# ---------------------------------------------------------------------------
# Stub uvicorn so we can capture the command line without a real Python env.
# We put a fake "uvicorn" ahead of the real one (or the missing one) on PATH.
# ---------------------------------------------------------------------------
TMPDIR_STUB="$(mktemp -d)"
STUB_BIN="$TMPDIR_STUB/uvicorn"

# Stub writes the full argv to a file for inspection, then exits 0.
cat > "$STUB_BIN" << 'EOF'
#!/bin/sh
echo "$@" > "$STUB_CAPTURE_FILE"
EOF
chmod +x "$STUB_BIN"
ORIG_PATH="$PATH"
export PATH="$TMPDIR_STUB:$PATH"

# Also stub getent so we can simulate DNS outcomes without a real network.
STUB_GETENT="$TMPDIR_STUB/getent"

# ---- Test 1: UVICORN_FORWARDED_ALLOW_IPS override is honoured ---------------
STUB_CAPTURE="$TMPDIR_STUB/capture_t1.txt"
export STUB_CAPTURE_FILE="$STUB_CAPTURE"

UVICORN_FORWARDED_ALLOW_IPS="10.0.0.99" \
    sh "$SCRIPT" "app.main:app" --host 0.0.0.0 --port 8000 > /dev/null 2>&1
ARGV="$(cat "$STUB_CAPTURE" 2>/dev/null || echo '')"
if echo "$ARGV" | grep -q -- '--forwarded-allow-ips=10.0.0.99'; then
    _pass "env-var override sets --forwarded-allow-ips=10.0.0.99"
else
    _fail "env-var override: expected --forwarded-allow-ips=10.0.0.99, got: $ARGV"
fi
unset UVICORN_FORWARDED_ALLOW_IPS

# ---- Test 2: --proxy-headers flag is always present -------------------------
if echo "$ARGV" | grep -q -- '--proxy-headers'; then
    _pass "--proxy-headers present when env-var override used"
else
    _fail "--proxy-headers missing when env-var override used. Args: $ARGV"
fi

# ---- Test 3: module:app target is the first uvicorn argument ----------------
FIRST_ARG="$(echo "$ARGV" | awk '{print $1}')"
if [ "$FIRST_ARG" = "app.main:app" ]; then
    _pass "module:app target is the first uvicorn argument"
else
    _fail "expected first arg 'app.main:app', got: $FIRST_ARG"
fi

# ---- Test 4: extra args are passed through ----------------------------------
if echo "$ARGV" | grep -q -- '--host 0.0.0.0' && echo "$ARGV" | grep -q -- '--port 8000'; then
    _pass "extra args (--host, --port) are passed through"
else
    _fail "extra args missing from uvicorn command. Args: $ARGV"
fi

# ---- Test 5: DNS fallback to 127.0.0.1 when getent returns nothing ----------
# Provide a getent stub that simulates "no caddy DNS entry".
cat > "$STUB_GETENT" << 'EOF'
#!/bin/sh
# simulate DNS miss: output nothing, exit 1
exit 1
EOF
chmod +x "$STUB_GETENT"

STUB_CAPTURE="$TMPDIR_STUB/capture_t5.txt"
export STUB_CAPTURE_FILE="$STUB_CAPTURE"

sh "$SCRIPT" "retrieval_api.main:app" --host 0.0.0.0 --port 8040 > /dev/null 2>&1
ARGV5="$(cat "$STUB_CAPTURE" 2>/dev/null || echo '')"
if echo "$ARGV5" | grep -q -- '--forwarded-allow-ips=127.0.0.1'; then
    _pass "DNS miss falls back to --forwarded-allow-ips=127.0.0.1"
else
    _fail "DNS miss fallback: expected --forwarded-allow-ips=127.0.0.1, got: $ARGV5"
fi

# ---- Test 6: DNS success uses resolved IP -----------------------------------
# Provide a getent stub that simulates a successful Caddy DNS resolution.
cat > "$STUB_GETENT" << 'EOF'
#!/bin/sh
# simulate successful caddy DNS resolution
echo "172.20.0.5   caddy"
EOF
chmod +x "$STUB_GETENT"

STUB_CAPTURE="$TMPDIR_STUB/capture_t6.txt"
export STUB_CAPTURE_FILE="$STUB_CAPTURE"

sh "$SCRIPT" "knowledge_ingest.app:app" --host 0.0.0.0 --port 8000 > /dev/null 2>&1
ARGV6="$(cat "$STUB_CAPTURE" 2>/dev/null || echo '')"
if echo "$ARGV6" | grep -q -- '--forwarded-allow-ips=172.20.0.5'; then
    _pass "DNS success uses resolved IP --forwarded-allow-ips=172.20.0.5"
else
    _fail "DNS success: expected --forwarded-allow-ips=172.20.0.5, got: $ARGV6"
fi

# ---- Test 7: missing first argument causes non-zero exit --------------------
if sh "$SCRIPT" 2>/dev/null; then
    _fail "missing module:app argument should exit non-zero"
else
    _pass "missing module:app argument exits non-zero"
fi

# ---- Cleanup ----------------------------------------------------------------
rm -rf "$TMPDIR_STUB"
export PATH="$ORIG_PATH"

# ---- Summary ----------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
exit 0
