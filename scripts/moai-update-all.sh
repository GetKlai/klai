#!/bin/bash
# moai-update-all.sh -- Update MoAI in all initialized projects, then sync klai assets
#
# Usage: ./klai-claude/scripts/moai-update-all.sh
#
# Finds all directories with .moai/ and runs `moai update --yes` in each.
# Then syncs klai-specific assets from klai-claude to the monorepo root.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "Updating MoAI binary..."
moai update --binary --yes 2>&1 | tail -3
echo ""

for dir in "$ROOT" "$ROOT"/klai-*/; do
    if [ -d "$dir/.moai" ]; then
        name=$(basename "$dir")
        [ "$dir" = "$ROOT" ] && name="root"
        echo "Syncing templates in $name..."
        (cd "$dir" && moai update --templates-only --yes 2>&1 | tail -3)
    fi
done

echo ""
echo "Syncing klai assets to root..."
"$SCRIPT_DIR/sync-to-root.sh"
