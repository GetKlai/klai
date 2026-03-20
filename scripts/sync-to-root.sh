#!/bin/bash
# sync-to-root.sh -- Sync klai-specific .claude/ assets from klai-claude to monorepo root
#
# Usage: ./klai-claude/scripts/sync-to-root.sh
#
# Syncs klai-built agents, commands, and rules to the root .claude/ directory.
# MoAI-managed files (agents/moai, commands/moai, etc.) are NOT touched --
# those are managed by `moai update`.
#
# Rules use symlinks (single source of truth in klai-claude/rules/).
# Agents and commands use copies (may diverge from canonical source).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="$(cd "$SRC/.." && pwd)"

if [ ! -d "$ROOT/.claude" ]; then
    echo "Error: $ROOT/.claude does not exist. Run 'moai init .' first."
    exit 1
fi

echo "Syncing klai assets from klai-claude to root .claude/..."

# Klai agents (copy)
mkdir -p "$ROOT/.claude/agents/klai"
cp "$SRC/agents/klai/"*.md "$ROOT/.claude/agents/klai/" 2>/dev/null && \
    echo "  agents/klai/ synced (copy)" || echo "  agents/klai/ -- no files found"

# Klai commands (copy)
mkdir -p "$ROOT/.claude/commands/klai"
cp "$SRC/commands/klai/"*.md "$ROOT/.claude/commands/klai/" 2>/dev/null && \
    echo "  commands/klai/ synced (copy)" || echo "  commands/klai/ -- no files found"

# Klai rules (symlink -- canonical source uses root-relative paths)
if [ -L "$ROOT/.claude/rules/klai" ]; then
    echo "  rules/klai/ already symlinked"
elif [ -d "$ROOT/.claude/rules/klai" ]; then
    rm -rf "$ROOT/.claude/rules/klai"
    ln -sfn ../../klai-claude/rules/klai "$ROOT/.claude/rules/klai"
    echo "  rules/klai/ replaced copies with symlink"
else
    ln -sfn ../../klai-claude/rules/klai "$ROOT/.claude/rules/klai"
    echo "  rules/klai/ symlinked"
fi

# GTM rules (symlink -- canonical source uses root-relative paths)
if [ -L "$ROOT/.claude/rules/gtm" ]; then
    echo "  rules/gtm/ already symlinked"
elif [ -d "$ROOT/.claude/rules/gtm" ]; then
    rm -rf "$ROOT/.claude/rules/gtm"
    ln -sfn ../../klai-claude/rules/gtm "$ROOT/.claude/rules/gtm"
    echo "  rules/gtm/ replaced copies with symlink"
else
    ln -sfn ../../klai-claude/rules/gtm "$ROOT/.claude/rules/gtm"
    echo "  rules/gtm/ symlinked"
fi

echo "Done."
