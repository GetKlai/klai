#!/bin/bash
# update-moai.sh — Update MoAI-ADK agents to a new upstream version
#
# Usage: ./scripts/update-moai.sh
#
# MoAI-ADK is distributed via https://github.com/moai-adk/moai-adk
# After updating: check whether agents/klai/ or rules/klai/ need adjustments.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR=$(mktemp -d)

MOAI_REPO="https://github.com/modu-ai/moai-adk.git"

echo "Updating MoAI-ADK..."
echo "Current version: $(cat "$ROOT_DIR/VERSION")"
echo ""

# Clone upstream MoAI
echo "Fetching upstream..."
git clone --depth 1 "$MOAI_REPO" "$TMP_DIR" 2>/dev/null || {
    echo "Error: could not fetch MoAI-ADK repo."
    echo "Check your internet connection and whether the repo is reachable."
    rm -rf "$TMP_DIR"
    exit 1
}

# Show diff
echo ""
echo "Changes compared to current agents/moai/:"
diff -rq "$ROOT_DIR/agents/moai/" "$TMP_DIR/.claude/agents/moai/" 2>/dev/null || true
echo ""

read -p "Continue with update? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Update cancelled."
    rm -rf "$TMP_DIR"
    exit 0
fi

# Replace agents/moai completely
rm -rf "$ROOT_DIR/agents/moai"
cp -r "$TMP_DIR/.claude/agents/moai" "$ROOT_DIR/agents/moai/"

# Replace rules/moai if present
if [ -d "$TMP_DIR/.claude/rules/moai" ]; then
    rm -rf "$ROOT_DIR/rules/moai"
    cp -r "$TMP_DIR/.claude/rules/moai" "$ROOT_DIR/rules/moai/"
fi

# Replace skills if present
if [ -d "$TMP_DIR/.claude/skills" ]; then
    rm -rf "$ROOT_DIR/skills"
    cp -r "$TMP_DIR/.claude/skills" "$ROOT_DIR/skills/"
fi

rm -rf "$TMP_DIR"

echo "MoAI agents updated."
echo "Check rules/klai/ and agents/klai/ for any required adjustments."
echo "Don't forget to update VERSION and commit."
