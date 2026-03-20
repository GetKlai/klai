#!/bin/bash
# sync-to-root.sh -- Sync klai-specific .claude/ assets from klai-claude to monorepo root
#
# Usage: ./klai-claude/scripts/sync-to-root.sh
#
# Copies klai-built agents, commands, and rules to the root .claude/ directory.
# MoAI-managed files (agents/moai, commands/moai, etc.) are NOT touched --
# those are managed by `moai update`.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"

if [ ! -d "$ROOT/.claude" ]; then
    echo "Error: $ROOT/.claude does not exist. Run 'moai init .' first."
    exit 1
fi

echo "Syncing klai assets from klai-claude to root .claude/..."

# Klai agents
mkdir -p "$ROOT/.claude/agents/klai"
cp "$SRC/agents/klai/"*.md "$ROOT/.claude/agents/klai/" 2>/dev/null && \
    echo "  agents/klai/ synced" || echo "  agents/klai/ -- no files found"

# Klai commands
mkdir -p "$ROOT/.claude/commands/klai"
cp "$SRC/commands/klai/"*.md "$ROOT/.claude/commands/klai/" 2>/dev/null && \
    echo "  commands/klai/ synced" || echo "  commands/klai/ -- no files found"

# Klai rules (root versions with monorepo-relative paths)
mkdir -p "$ROOT/.claude/rules/klai"
cp "$SRC/rules/klai/"*.md "$ROOT/.claude/rules/klai/" 2>/dev/null && \
    echo "  rules/klai/ synced" || echo "  rules/klai/ -- no files found"

echo "Done."
