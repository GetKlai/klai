#!/usr/bin/env bash
# SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02: prevent re-introduction of a
# hardcoded copy of the grounded chat system prompt anywhere outside
# the canonical klai-libs/chat-prompts library.
#
# Failure mode this catches: a future PR adds a new chat surface
# (third service, new endpoint) and inlines the prompt instead of
# importing klai_chat_prompts. The two existing copies in
# synthesis.py + partner_chat.py would have been kept in sync by the
# original NL/EN switch lint; this lint covers the next version.
#
# Run by per-service CI on every PR (portal-api.yml, retrieval-api.yml).
# Local: ./scripts/lint-no-duplicate-chat-prompt.sh
#
# Exit codes:
#   0 - no duplication found
#   1 - hardcoded prompt found outside klai-libs/chat-prompts
set -euo pipefail

# Anchor strings unique enough to identify a copy of the grounded chat
# prompt without false positives. Pick phrases that have no legitimate
# non-prompt use in the codebase.
ANCHORS=(
    "Detect the language of the user's most recent SUBSTANTIVE message"
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands"
)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Files under these paths are allowed to contain the anchors:
# - the canonical library itself
# - the SPEC documentation
# - this script
# - klai .claude rules + docs (audit reports, retros, architecture docs)
ALLOWED_PATTERN='^(klai-libs/chat-prompts/|\.moai/specs/SPEC-RAG-MULTILINGUAL-CHAT-001/|scripts/lint-no-duplicate-chat-prompt\.sh|\.claude/rules/klai/|docs/architecture/|docs/retros/|docs/audit-|docs/research/kb-chat-system-prompts\.md)'

# Excludes for traversal speed + correctness. These are paths we never
# want to scan (build artefacts, vendored deps).
EXCLUDES=(
    --exclude-dir=.venv
    --exclude-dir=node_modules
    --exclude-dir=dist
    --exclude-dir=build
    --exclude-dir=__pycache__
    --exclude-dir=.eggs
    --exclude-dir=.git
    --exclude-dir=.mypy_cache
    --exclude-dir=.pytest_cache
    --exclude-dir=.ruff_cache
    --exclude-dir=.serena
    --exclude-dir=target
    -I  # ignore binary files
)

violations=0
for anchor in "${ANCHORS[@]}"; do
    # grep -rF (recursive, fixed string), suppress errors, list filenames + line numbers.
    raw_matches=$(grep -rFn "${EXCLUDES[@]}" -- "$anchor" . 2>/dev/null || true)

    # Filter: drop matches in allowed paths. Strip the leading "./" first.
    filtered=$(echo "$raw_matches" | sed 's|^\./||' | grep -Ev "$ALLOWED_PATTERN" || true)

    if [[ -n "$filtered" ]]; then
        echo "FAIL: hardcoded chat-prompt anchor found outside klai-libs/chat-prompts:"
        echo "  anchor: $anchor"
        echo "$filtered" | sed 's/^/    /'
        echo
        violations=$((violations + 1))
    fi
done

if (( violations > 0 )); then
    echo
    echo "SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02 violation."
    echo "The grounded chat system prompt has a single source of truth:"
    echo "  klai-libs/chat-prompts/klai_chat_prompts/__init__.py"
    echo
    echo "Replace the hardcoded copy with:"
    echo "  from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT"
    exit 1
fi

echo "OK: no duplicate chat-prompt strings outside klai-libs/chat-prompts."
exit 0
