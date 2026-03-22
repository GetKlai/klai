#!/bin/bash
# setup.sh — Set up the Klai workspace on a new machine
#
# Usage (from the projects/ directory):
#   git clone git@github.com:GetKlai/klai-claude.git klai/klai-claude
#   ./klai/klai-claude/scripts/setup.sh
#
# What this script does:
#   1. Determines the klai/ workspace root
#   2. Clones missing repos
#   3. Creates klai/CLAUDE.md (shared base instructions auto-loaded by Claude Code)
#   4. Runs update-shared.sh in each project

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KLAI_CLAUDE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
KLAI_ROOT="$(cd "$KLAI_CLAUDE_DIR/.." && pwd)"
GH_ORG="git@github.com:GetKlai"

echo "Klai workspace: $KLAI_ROOT"
echo ""

# Repos to clone — name:repo pairs (bash 3.2 compatible)
REPOS="klai-website klai-infra klai-app"

for NAME in $REPOS; do
    TARGET="$KLAI_ROOT/$NAME"
    if [ -d "$TARGET/.git" ]; then
        echo "Already present: $NAME"
    else
        echo "Cloning: $NAME..."
        git clone "$GH_ORG/$NAME.git" "$TARGET" 2>/dev/null \
            && echo "  Done: $NAME" \
            || echo "  Skipped: $NAME (repo may not exist yet)"
    fi
done

echo ""

# Create klai/CLAUDE.md from klai-claude/CLAUDE.md
# Claude Code auto-loads this for all projects inside the klai/ workspace
PARENT_CLAUDE="$KLAI_ROOT/CLAUDE.md"
echo "Creating workspace CLAUDE.md..."
cp "$KLAI_CLAUDE_DIR/CLAUDE.md" "$PARENT_CLAUDE"
echo "  Created: $PARENT_CLAUDE"

echo ""

# Run update-shared.sh in each project that has it
for NAME in $REPOS; do
    TARGET="$KLAI_ROOT/$NAME"
    SCRIPT="$TARGET/scripts/update-shared.sh"
    if [ -f "$SCRIPT" ]; then
        echo "Installing shared configuration in $NAME..."
        bash "$SCRIPT"
    fi
done

echo ""
echo "Setup complete. Open a project in Claude Code:"
echo "  code $KLAI_ROOT/klai-website"
