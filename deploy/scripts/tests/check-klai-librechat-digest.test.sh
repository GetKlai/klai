#!/bin/sh
# Tests for check-klai-librechat-digest.sh.
# Run: sh deploy/scripts/tests/check-klai-librechat-digest.test.sh

set -eu

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)
guard="$repo_root/deploy/check-klai-librechat-digest.sh"

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

echo "check-klai-librechat-digest.sh"

# 1. Digest form: accepted.
cat >"$work/good.yml" <<'EOF'
  librechat-getklai:
    image: ghcr.io/getklai/librechat@sha256:7d8bb076261153e20e69e04ff95f599c1002e59b782afe82e8a1a17e4058f82a
EOF
run "$work/good.yml"
check "accepts a digest reference" 0 "$rc"

# 2. Tag form: rejected. This is the incident.
cat >"$work/bad.yml" <<'EOF'
  librechat-getklai:
    image: ghcr.io/getklai/librechat:v0.8.7-klai.1
EOF
run "$work/bad.yml"
check "rejects a tag reference" 1 "$rc"
grep -q 'v0.8.7-klai.1' "$work/out" || {
    echo "  FAIL rejection does not name the offending line"
    failures=$((failures + 1))
}

# 3. Even a commit-suffixed tag is rejected: immutable by convention is not
#    immutable by construction, and the whole point is to remove the judgement
#    call at the consuming end.
cat >"$work/suffixed.yml" <<'EOF'
    image: ghcr.io/getklai/librechat:v0.8.7-klai.1-cc75acb0d37c
EOF
run "$work/suffixed.yml"
check "rejects even a commit-suffixed tag" 1 "$rc"

# 4. A comment explaining the rule must not trip the rule.
cat >"$work/comment.yml" <<'EOF'
    # never: ghcr.io/getklai/librechat:some-tag
    image: ghcr.io/getklai/librechat@sha256:7d8bb076261153e20e69e04ff95f599c1002e59b782afe82e8a1a17e4058f82a
EOF
run "$work/comment.yml"
check "ignores commented-out examples" 0 "$rc"

# 5. A file that never mentions the image is fine.
cat >"$work/unrelated.yml" <<'EOF'
    image: ghcr.io/danny-avila/librechat:v0.8.7
EOF
run "$work/unrelated.yml"
check "ignores files without the Klai image" 0 "$rc"

# 6. A truncated digest must not pass as one.
cat >"$work/short.yml" <<'EOF'
    image: ghcr.io/getklai/librechat@sha256:7d8bb076
EOF
run "$work/short.yml"
check "rejects a truncated digest" 1 "$rc"

if [ "$failures" -ne 0 ]; then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
