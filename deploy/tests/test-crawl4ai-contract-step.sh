#!/bin/sh
# Tests for crawl4ai-contract-step.sh: only the script's SKIPPED exit (3) may
# end green with a warning; a contract break (1) and a stray 2 must fail.
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail=0

check() {
    want_exit=$1 stub_exit=$2 want_summary=$3
    printf 'exit %s\n' "$stub_exit" > "$TMP/stub.sh"
    : > "$TMP/summary.md"
    GITHUB_STEP_SUMMARY="$TMP/summary.md" sh "$HERE/crawl4ai-contract-step.sh" sh "$TMP/stub.sh" > "$TMP/out.txt" 2>&1
    got=$?
    if [ "$got" -ne "$want_exit" ] || ! grep -q "$want_summary" "$TMP/summary.md"; then
        echo "FAIL: stub exit $stub_exit -> step exit $got (want $want_exit, summary '$want_summary')"
        fail=1
    else
        echo "ok: stub exit $stub_exit -> step exit $got"
    fi
}

check 0 0 'cookie contract: OK'
check 1 1 'BROKEN (exit 1)'
check 2 2 'BROKEN (exit 2)'
check 0 3 'SKIPPED'
grep -q '::warning::' "$TMP/out.txt" || { echo "FAIL: skip printed no warning"; fail=1; }
exit "$fail"
