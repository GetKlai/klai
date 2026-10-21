#!/usr/bin/env bash
# SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02 + REQ-11.2: prevent re-introduction
# of hardcoded copies of any system-prompt content that should live in
# exactly one canonical location.
#
# Two anchor sets:
#   1. Grounded chat prompt anchors — canonical home is
#      klai-libs/chat-prompts. Imported by partner_chat.py + synthesis.py.
#   2. KB prefix anchors (Klai Knowledge Base header, ANSWER FORMAT
#      instructions, KB-unavailable notice, Klai Templates wrapper) —
#      canonical home is klai-libs/chat-prompts. The LiteLLM hook that used
#      to carry them was removed on 2026-10-21.
#
# Failure mode this catches: a future PR adds a new chat surface or
# duplicates a prefix block into a second place. CI rejects the PR.
#
# Run by per-service CI on every PR. Local: this script.
#
# Exit codes:
#   0 - no duplication found
#   1 - hardcoded anchor found in a disallowed location
set -euo pipefail

# Set 1 — grounded chat prompt anchors (canonical: klai-libs/chat-prompts).
GROUNDED_ANCHORS=(
    "Detect the language of the user's most recent SUBSTANTIVE message"
    "Als de gebruiker Nederlands schrijft, antwoord je in het Nederlands"
)

# Set 2 — KB prefix anchors (canonical home: klai-libs/chat-prompts).
#
# Phase 4 (REQ-10) rewrote these blocks from NL-only to English-prefixed
# multilingual instructions. The model receives English instructions but
# answers in the language detected by GROUNDED_CHAT_SYSTEM_PROMPT (set 1
# above). The five anchors below are the new canonical strings — never
# duplicate them in any other file.
HOOK_ANCHORS=(
    "Klai Knowledge Base — use this as supplementary context"
    "Klai Knowledge Base — answer strictly using only the sources below"
    "Klai Knowledge Base — TEMPORARILY UNAVAILABLE"
    "Klai Templates — apply the following instructions to your answer"
    "ANSWER FORMAT — always follow this"
)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Allowed paths for the GROUNDED anchors (set 1):
#   - the canonical library itself
#   - the SPEC documentation
#   - this script
#   - klai .claude rules + docs
GROUNDED_ALLOWED='^(klai-libs/chat-prompts/|docs/specs/|scripts/lint-no-duplicate-chat-prompt\.sh|\.claude/rules/klai/|docs/architecture/|docs/runbooks/|docs/retros/|docs/audit-|docs/research/kb-chat-system-prompts\.md)'

# Allowed paths for the KB prefix anchors (set 2):
#   - the shared library (canonical home)
#   - this script
#   - SPEC + klai rules + docs (they describe what the chat says)
HOOK_ALLOWED='^(klai-libs/chat-prompts/|docs/specs/|scripts/lint-no-duplicate-chat-prompt\.sh|\.claude/rules/klai/|docs/architecture/|docs/runbooks/|docs/retros/|docs/audit-|docs/research/kb-chat-system-prompts\.md)'

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
    --exclude-dir=.context
    --exclude-dir=.mypy_cache
    --exclude-dir=.pytest_cache
    --exclude-dir=.ruff_cache
    --exclude-dir=.serena
    --exclude-dir=target
    -I  # ignore binary files
)

check_anchor_set () {
    local label="$1"
    local allowed_re="$2"
    local canonical_hint="$3"
    shift 3
    local anchors=("$@")
    local local_violations=0
    for anchor in "${anchors[@]}"; do
        local raw_matches
        raw_matches=$(grep -rFn "${EXCLUDES[@]}" -- "$anchor" . 2>/dev/null || true)
        local filtered
        filtered=$(echo "$raw_matches" | sed 's|^\./||' | grep -Ev "$allowed_re" || true)
        if [[ -n "$filtered" ]]; then
            echo "FAIL: ${label} anchor found in a disallowed location:"
            echo "  anchor: $anchor"
            echo "$filtered" | sed 's/^/    /'
            echo "  canonical home: ${canonical_hint}"
            echo
            local_violations=$((local_violations + 1))
        fi
    done
    return "$local_violations"
}

violations=0
check_anchor_set \
    "GROUNDED chat prompt" \
    "$GROUNDED_ALLOWED" \
    "klai-libs/chat-prompts/klai_chat_prompts/__init__.py — import via 'from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT'" \
    "${GROUNDED_ANCHORS[@]}" || violations=$((violations + $?))

check_anchor_set \
    "KB prefix" \
    "$HOOK_ALLOWED" \
    "klai-libs/chat-prompts" \
    "${HOOK_ANCHORS[@]}" || violations=$((violations + $?))

if (( violations > 0 )); then
    echo
    echo "SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02 / REQ-11.2 violation."
    echo "Each system-prompt anchor must live in exactly one canonical location."
    echo "If a prefix block moved to a new canonical home, update HOOK_ALLOWED"
    echo "at the top of this script accordingly."
    exit 1
fi

echo "OK: no duplicate prompt anchors outside their canonical homes."
exit 0
