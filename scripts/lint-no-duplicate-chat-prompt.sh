#!/usr/bin/env bash
# SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02 + REQ-11.2: prevent re-introduction
# of hardcoded copies of any system-prompt content that should live in
# exactly one canonical location.
#
# Two anchor sets:
#   1. Grounded chat prompt anchors — canonical home is
#      klai-libs/chat-prompts. Imported by partner_chat.py + synthesis.py
#      (and post-REQ-10, by deploy/litellm/klai_knowledge.py).
#   2. LiteLLM-hook NL prefix anchors — canonical home is
#      deploy/litellm/klai_knowledge.py. These prefix blocks (Klai
#      Kennisbank header, ANTWOORDFORMAAT instructions, KB-unavailable
#      notice, Klai Templates wrapper) used to be NL-only and v1.2 of
#      SPEC-RAG-MULTILINGUAL-CHAT-001 either rewrites them in-place or
#      moves them into klai-libs/chat-prompts as a second exported
#      constant. Either way, exactly ONE file in the repo may contain
#      them — never two.
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

# Set 2 — LiteLLM-hook prefix anchors (canonical homes:
# deploy/litellm/klai_kb_answer_policy.py and
# deploy/litellm/klai_kb_context_prompt.py).
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
#   - the LiteLLM-vendored copy (Phase 4 REQ-10 — sync enforced by
#     deploy/litellm/tests/test_klai_chat_prompts_drift.py, same pattern
#     as klai_service_auth.py from SPEC-SEC-SERVICE-AUTH-001 Phase C-1)
#   - the SPEC documentation
#   - this script
#   - klai .claude rules + docs
#
# NOT allowed: deploy/litellm/klai_knowledge.py — the hook imports
# ``GROUNDED_CHAT_SYSTEM_PROMPT`` from the vendored copy, never inlines.
# The 2026-05-07 inline-hotfix was reverted by the cleanup PR after the
# deploy workflow was switched from ``docker compose restart litellm`` to
# ``/opt/klai/scripts/compose-up.sh litellm`` (= ``docker compose up -d
# --remove-orphans litellm``), which picks up new bind-mounts
# automatically. Two canonical homes for this anchor: canonical library
# and vendored copy. Drift between them is enforced by
# test_klai_chat_prompts_drift.py.
GROUNDED_ALLOWED='^(klai-libs/chat-prompts/|deploy/litellm/klai_chat_prompts\.py|deploy/litellm/tests/|\.moai/specs/SPEC-RAG-MULTILINGUAL-CHAT-001/|scripts/lint-no-duplicate-chat-prompt\.sh|\.claude/rules/klai/|docs/architecture/|docs/runbooks/|docs/retros/|docs/audit-|docs/research/kb-chat-system-prompts\.md)'

# Allowed paths for the HOOK anchors (set 2):
#   - the LiteLLM hook/policy/context-prompt files (canonical homes)
#   - the shared library (if the v1.2 implementation moved the blocks there)
#   - this script
#   - SPEC + klai rules + docs (they describe what the hook says)
#   - LiteLLM hook tests
HOOK_ALLOWED='^(deploy/litellm/klai_knowledge\.py|deploy/litellm/klai_kb_answer_policy\.py|deploy/litellm/klai_kb_context_prompt\.py|deploy/litellm/test_|deploy/litellm/tests/|klai-libs/chat-prompts/|\.moai/specs/SPEC-RAG-MULTILINGUAL-CHAT-001/|scripts/lint-no-duplicate-chat-prompt\.sh|\.claude/rules/klai/|docs/architecture/|docs/runbooks/|docs/retros/|docs/audit-|docs/research/kb-chat-system-prompts\.md)'

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
    "LiteLLM-hook NL prefix" \
    "$HOOK_ALLOWED" \
    "deploy/litellm/klai_knowledge.py (or klai-libs/chat-prompts if v1.2 migrated the blocks there)" \
    "${HOOK_ANCHORS[@]}" || violations=$((violations + $?))

if (( violations > 0 )); then
    echo
    echo "SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02 / REQ-11.2 violation."
    echo "Each system-prompt anchor must live in exactly one canonical location."
    echo "If the v1.2 LiteLLM-hook rework moved a NL prefix block into the shared"
    echo "library, update HOOK_ALLOWED at the top of this script accordingly."
    exit 1
fi

echo "OK: no duplicate prompt anchors outside their canonical homes."
exit 0
