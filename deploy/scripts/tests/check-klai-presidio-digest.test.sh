#!/bin/sh
# Tests for check-klai-presidio-digest.sh.
# Run: sh deploy/scripts/tests/check-klai-presidio-digest.test.sh

set -eu

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)
guard="$repo_root/deploy/check-klai-presidio-digest.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0

check() {
    if [ "$2" = "$3" ]; then
        echo "  ok   $1"
    else
        echo "  FAIL $1 (expected exit $2, got $3)"
        failures=$((failures + 1))
    fi
}

run() {
    set +e
    sh "$guard" "$1" >"$work/out" 2>&1
    rc=$?
    set -e
}

echo "check-klai-presidio-digest.sh"

# 1. Digest form: accepted.
cat >"$work/good.yml" <<'EOF'
  presidio-analyzer:
    image: ghcr.io/getklai/presidio-analyzer@sha256:7d8bb076261153e20e69e04ff95f599c1002e59b782afe82e8a1a17e4058f82a
EOF
run "$work/good.yml"
check "accepts a digest reference" 0 "$rc"

# 2. Tag form: rejected.
cat >"$work/bad.yml" <<'EOF'
  presidio-analyzer:
    image: ghcr.io/getklai/presidio-analyzer:2.2.362-klai.1
EOF
run "$work/bad.yml"
check "rejects a tag reference" 1 "$rc"
grep -q '2.2.362-klai.1' "$work/out" || {
    echo "  FAIL rejection does not name the offending line"
    failures=$((failures + 1))
}

# 3. A comment explaining the rule must not trip the rule.
cat >"$work/comment.yml" <<'EOF'
    # never: ghcr.io/getklai/presidio-analyzer:some-tag
    image: ghcr.io/getklai/presidio-analyzer@sha256:7d8bb076261153e20e69e04ff95f599c1002e59b782afe82e8a1a17e4058f82a
EOF
run "$work/comment.yml"
check "ignores commented-out examples" 0 "$rc"

# 4. A file that never mentions the image is fine.
cat >"$work/unrelated.yml" <<'EOF'
    image: ghcr.io/data-privacy-stack/presidio-analyzer@sha256:286e3fa7f3a7426e775e8564fe1870f1ba8f999d3ab8bbb8cc46a44355d9d6e9
EOF
run "$work/unrelated.yml"
check "ignores files without the Klai image (stock base image is fine)" 0 "$rc"

# 5. A truncated digest must not pass as one.
cat >"$work/short.yml" <<'EOF'
    image: ghcr.io/getklai/presidio-analyzer@sha256:7d8bb076
EOF
run "$work/short.yml"
check "rejects a truncated digest" 1 "$rc"

# 6. A well-formed placeholder is REJECTED. It is 64 hex characters, so the
# format check alone passes it — and nothing else in CI would catch it either,
# because check-image-pullable.sh only matches `image:tag` form and is blind to
# every digest-pinned reference. A placeholder reaching main would deploy an
# unpullable image to core-01 and take the service down, so the bootstrap gap
# has to fail loudly here rather than be tolerated as an "interim state".
cat >"$work/placeholder.yml" <<'EOF'
    image: ghcr.io/getklai/presidio-analyzer@sha256:0000000000000000000000000000000000000000000000000000000000000000
EOF
run "$work/placeholder.yml"
check "rejects a well-formed placeholder digest" 1 "$rc"

if [ "$failures" -ne 0 ]; then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
