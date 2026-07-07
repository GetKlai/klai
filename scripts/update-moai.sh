#!/bin/bash
# update-moai.sh — Update MoAI-ADK assets to a new upstream version
#
# Usage: ./scripts/update-moai.sh [tag]
#
# MoAI-ADK is distributed via https://github.com/modu-ai/moai-adk
# This script updates MoAI-owned assets only. It deliberately does not touch
# AGENTS.md, .agents/codex, .claude/rules/klai, or .claude/settings.json.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR=$(mktemp -d)
TARGET_TAG="${1:-v2.14.0}"

MOAI_REPO="https://github.com/modu-ai/moai-adk.git"
OVERLAY_DIR="$TMP_DIR/klai-overlays"
STAGING_DIR="$TMP_DIR/staging"

OVERLAY_FILES=(
    ".claude/commands/moai/e2e.md"
    ".claude/rules/moai/workflow/workflow-modes.md"
    ".claude/skills/moai/workflows/sync.md"
    ".claude/hooks/moai/handle-permission-denied.sh"
)

UPSTREAM_CONFIG_SECTIONS=(
    "context.yaml"
    "design.yaml"
    "interview.yaml"
    "lsp.yaml"
    "research.yaml"
    "security.yaml"
    "sunset.yaml"
)

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

copy_tree_atomic() {
    local source="$1"
    local target="$2"
    local name
    name="$(basename "$target")"

    if [ ! -d "$source" ]; then
        echo "Error: missing upstream directory: $source" >&2
        exit 1
    fi

    rm -rf "$STAGING_DIR/$name"
    mkdir -p "$STAGING_DIR" "$(dirname "$target")"
    cp -R "$source" "$STAGING_DIR/$name"
    rm -rf "$target"
    mv "$STAGING_DIR/$name" "$target"
}

copy_file_if_present() {
    local source="$1"
    local target="$2"

    if [ -f "$source" ]; then
        mkdir -p "$(dirname "$target")"
        cp "$source" "$target"
    fi
}

save_overlay_files() {
    local rel

    mkdir -p "$OVERLAY_DIR"
    for rel in "${OVERLAY_FILES[@]}"; do
        if [ -e "$ROOT_DIR/$rel" ]; then
            mkdir -p "$OVERLAY_DIR/$(dirname "$rel")"
            cp -R "$ROOT_DIR/$rel" "$OVERLAY_DIR/$rel"
        fi
    done
}

restore_overlay_files() {
    local rel

    for rel in "${OVERLAY_FILES[@]}"; do
        if [ -e "$OVERLAY_DIR/$rel" ]; then
            mkdir -p "$(dirname "$ROOT_DIR/$rel")"
            rm -rf "$ROOT_DIR/$rel"
            cp -R "$OVERLAY_DIR/$rel" "$ROOT_DIR/$rel"
        fi
    done
}

normalize_hook_paths() {
    local hook

    for hook in "$ROOT_DIR"/.claude/hooks/moai/*.sh; do
        [ -f "$hook" ] || continue
        perl -0pi -e 's#/Users/[^/\n"]+/go/bin/moai#\$HOME/go/bin/moai#g; s/\n# Try detected Go bin path from initialization\nif \[ -f "\$HOME/go/bin/moai" \]; then\n[ \t]*exec "\$HOME/go/bin/moai" hook ([^<\n]+) < "\$temp_file" 2>\/dev\/null\nfi\n/\n/g' "$hook"
    done
}

normalize_skill_paths() {
    local skill="$ROOT_DIR/.claude/skills/moai/SKILL.md"

    if [ -f "$skill" ]; then
        perl -0pi -e 's#/Users/[^/\n"]+/MoAI/moai-adk-go/\.claude/skills/moai#\${CLAUDE_SKILL_DIR}#g' "$skill"
    fi
}

ensure_codex_reference_note() {
    local reference="$ROOT_DIR/.claude/skills/moai-foundation-core/modules/agents-reference.md"

    if [ -f "$reference" ] && ! grep -q "Klai overlay note (2026-07-07)" "$reference"; then
        perl -0pi -e 's/(Last Updated:[^\n]*\nVersion:[^\n]*\n)/$1\n> Klai overlay note (2026-07-07): this upstream reference is stale relative to\n> the actual MoAI-ADK v2.14.0 `.claude\/agents\/moai\/` catalog. Mentions of\n> `ai-codex` below are upstream documentation drift and do not mean official\n> MoAI Codex support. Klai'\''s Codex behavior lives in `AGENTS.md` and\n> `.agents\/codex\/README.md`.\n/s' "$reference"
    fi
}

echo "Updating MoAI-ADK..."
if [ -f "$ROOT_DIR/.moai/config/sections/system.yaml" ]; then
    echo "Current version: $(grep -E '^[[:space:]]+version:' "$ROOT_DIR/.moai/config/sections/system.yaml" | head -1 | sed 's/.*version:[[:space:]]*//')"
fi
echo "Target tag: $TARGET_TAG"
echo ""

echo "Fetching upstream..."
git clone --depth 1 --branch "$TARGET_TAG" "$MOAI_REPO" "$TMP_DIR/upstream" 2>/dev/null || {
    echo "Error: could not fetch MoAI-ADK repo."
    echo "Check your internet connection, tag name, and whether the repo is reachable."
    exit 1
}

UPSTREAM_DIR="$TMP_DIR/upstream"

echo ""
echo "Changes compared to current MoAI assets:"
diff -rq "$ROOT_DIR/.claude/agents/moai/" "$UPSTREAM_DIR/.claude/agents/moai/" 2>/dev/null || true
diff -rq "$ROOT_DIR/.claude/commands/moai/" "$UPSTREAM_DIR/.claude/commands/moai/" 2>/dev/null || true
diff -rq "$ROOT_DIR/.claude/rules/moai/" "$UPSTREAM_DIR/.claude/rules/moai/" 2>/dev/null || true
diff -rq "$ROOT_DIR/.claude/skills/moai/" "$UPSTREAM_DIR/.claude/skills/moai/" 2>/dev/null || true
diff -rq "$ROOT_DIR/.claude/hooks/moai/" "$UPSTREAM_DIR/.claude/hooks/moai/" 2>/dev/null || true
diff -rq "$ROOT_DIR/.claude/output-styles/moai/" "$UPSTREAM_DIR/.claude/output-styles/moai/" 2>/dev/null || true
diff -rq "$ROOT_DIR/.moai/config/astgrep-rules/" "$UPSTREAM_DIR/.moai/config/astgrep-rules/" 2>/dev/null || true
diff -rq "$ROOT_DIR/.moai/config/evaluator-profiles/" "$UPSTREAM_DIR/.moai/config/evaluator-profiles/" 2>/dev/null || true
echo ""

read -p "Continue with update? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Update cancelled."
    exit 0
fi

save_overlay_files

copy_tree_atomic "$UPSTREAM_DIR/.claude/agents/moai" "$ROOT_DIR/.claude/agents/moai"

if [ -d "$UPSTREAM_DIR/.claude/commands/moai" ]; then
    copy_tree_atomic "$UPSTREAM_DIR/.claude/commands/moai" "$ROOT_DIR/.claude/commands/moai"
fi

if [ -d "$UPSTREAM_DIR/.claude/rules/moai" ]; then
    copy_tree_atomic "$UPSTREAM_DIR/.claude/rules/moai" "$ROOT_DIR/.claude/rules/moai"
fi

if [ -d "$UPSTREAM_DIR/.claude/hooks/moai" ]; then
    copy_tree_atomic "$UPSTREAM_DIR/.claude/hooks/moai" "$ROOT_DIR/.claude/hooks/moai"
fi

if [ -d "$UPSTREAM_DIR/.claude/skills" ]; then
    for skill_dir in "$UPSTREAM_DIR"/.claude/skills/moai*; do
        [ -d "$skill_dir" ] || continue
        skill_name="$(basename "$skill_dir")"
        copy_tree_atomic "$skill_dir" "$ROOT_DIR/.claude/skills/$skill_name"
    done
fi

if [ -d "$UPSTREAM_DIR/.claude/output-styles/moai" ]; then
    mkdir -p "$ROOT_DIR/.claude/output-styles/moai"
    for style_file in "$UPSTREAM_DIR"/.claude/output-styles/moai/*; do
        [ -f "$style_file" ] || continue
        cp "$style_file" "$ROOT_DIR/.claude/output-styles/moai/$(basename "$style_file")"
    done
fi

if [ -d "$UPSTREAM_DIR/.moai/config/astgrep-rules" ]; then
    copy_tree_atomic "$UPSTREAM_DIR/.moai/config/astgrep-rules" "$ROOT_DIR/.moai/config/astgrep-rules"
fi

if [ -d "$UPSTREAM_DIR/.moai/config/evaluator-profiles" ]; then
    copy_tree_atomic "$UPSTREAM_DIR/.moai/config/evaluator-profiles" "$ROOT_DIR/.moai/config/evaluator-profiles"
fi

for section in "${UPSTREAM_CONFIG_SECTIONS[@]}"; do
    copy_file_if_present \
        "$UPSTREAM_DIR/.moai/config/sections/$section" \
        "$ROOT_DIR/.moai/config/sections/$section"
done

restore_overlay_files
normalize_hook_paths
normalize_skill_paths
ensure_codex_reference_note

echo "MoAI assets updated."
echo "Check Klai overlay patches before committing:"
echo "  - .claude/commands/moai/e2e.md local browser preflight"
echo "  - .claude/rules/moai/workflow/workflow-modes.md simplify notes"
echo "  - .claude/skills/moai/workflows/sync.md session boundary tags"
echo "  - .claude/hooks/moai/handle-permission-denied.sh local fail-open wrapper"
echo "  - .claude/skills/moai/SKILL.md local path normalization"
echo "  - .claude/skills/moai-foundation-core/modules/agents-reference.md Codex doc-rot note"
echo "  - .moai/config/sections/*.yaml project-specific values"
echo "  - AGENTS.md, .agents/codex, .claude/rules/klai, .claude/settings.json remain protected"
