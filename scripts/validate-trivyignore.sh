#!/bin/sh
# Forcing function — fail loud at commit / CI time when a `.trivyignore.yaml`
# entry omits required fields per SPEC-CI-TRIVY-POLICY-001 REQ-5.
#
# Why this exists: a Trivy ignore-list without rationale + expiry is just a
# silent allowlist. Industry-standard CVE management requires every exemption
# to carry both WHY (statement) and UNTIL WHEN (expired_at) so it bubbles
# back into the review queue on a known cadence.
#
# Validation logic lives in scripts/_validate_trivyignore.py for readability;
# this shell wrapper handles file discovery and mode detection.
#
# Modes (auto-detected):
#   - ci          : $GITHUB_ACTIONS=true → validate every .trivyignore.yaml
#                   in the working tree (GitHub Actions runs after checkout)
#   - pre-commit  : invoked from .githooks/pre-commit → validate only the
#                   files currently staged, reading their staged content
#                   via `git show :<path>`
#   - manual      : developer runs the script directly with paths as args

set -eu

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PY="$REPO_ROOT/scripts/_validate_trivyignore.py"

if [ ! -f "$PY" ]; then
    echo "[validate-trivyignore] FATAL: $PY not found." >&2
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "[validate-trivyignore] FATAL: python3 not available." >&2
    exit 2
fi

# ---- Resolve mode + file list ----

MODE="manual"
if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
    MODE="ci"
elif [ -n "${GIT_INDEX_FILE:-}" ] || git rev-parse --git-dir >/dev/null 2>&1 \
     && git diff --cached --name-only --diff-filter=ACM 2>/dev/null \
        | grep -qE '(^|/)\.trivyignore\.ya?ml$'; then
    MODE="pre-commit"
fi

FILES=""
case "$MODE" in
    pre-commit)
        FILES=$(git diff --cached --name-only --diff-filter=ACM \
                | grep -E '(^|/)\.trivyignore\.ya?ml$' || true)
        ;;
    ci)
        FILES=$(find . -name '.trivyignore.yaml' \
                  -not -path './.venv/*' \
                  -not -path './node_modules/*' \
                  -not -path './*/node_modules/*' \
                  -not -path './*/.venv/*' \
                  -not -path './.git/*' 2>/dev/null \
                | sed 's|^\./||' || true)
        ;;
    manual)
        if [ "$#" -gt 0 ]; then
            FILES="$*"
        else
            echo "Usage: $0 <path/to/.trivyignore.yaml> [...]" >&2
            exit 2
        fi
        ;;
esac

if [ -z "$FILES" ]; then
    echo "[validate-trivyignore] no .trivyignore.yaml files to validate — pass."
    exit 0
fi

# ---- Per-file validation ----

TODAY=$(date -u +%Y-%m-%d)
MAX_FUTURE_DAYS=365
MIN_STATEMENT_LEN=40

FAIL=0

for F in $FILES; do
    if [ "$MODE" = "pre-commit" ]; then
        # Staged content — may differ from working tree.
        if ! CONTENT=$(git show ":$F" 2>/dev/null); then
            echo "[validate-trivyignore] WARN: could not read staged content of $F — skipping."
            continue
        fi
    else
        if [ ! -f "$F" ]; then
            echo "[validate-trivyignore] WARN: $F not found — skipping."
            continue
        fi
        CONTENT=$(cat "$F")
    fi

    if ! printf '%s' "$CONTENT" | python3 "$PY" "$F" "$TODAY" "$MAX_FUTURE_DAYS" "$MIN_STATEMENT_LEN"; then
        FAIL=1
    fi
done

if [ "$FAIL" = "1" ]; then
    echo "" >&2
    echo "[validate-trivyignore] one or more .trivyignore.yaml files failed validation." >&2
    echo "Required per SPEC-CI-TRIVY-POLICY-001 REQ-5:" >&2
    echo "  - id           (CVE / GHSA / rule identifier)" >&2
    echo "  - statement    (≥${MIN_STATEMENT_LEN} chars rationale, no boilerplate)" >&2
    echo "  - expired_at   (YYYY-MM-DD, future, ≤${MAX_FUTURE_DAYS} days)" >&2
    exit 1
fi

exit 0
