#!/bin/bash
# Re-index all CodeIndex projects after kuzu→lbug format migration
# Run this AFTER stopping the app (bun run dev)

set -e
export PATH="/opt/homebrew/bin:/Users/spd/.nvm/versions/node/v24.7.0/bin:/usr/local/bin:/usr/bin:$PATH"

echo "=== CodeIndex Format Migration ==="
echo "Re-indexing all projects with current LadybugDB format..."
echo ""

# Remove old kuzu AND lbug database files (both might exist)
for name in CodeIndex ParrotKey ThePhrame codeindexweb domeinchecker goeiemorgen iphrame Sequencer sequencer-app coachconnect; do
  for dbfile in kuzu lbug; do
    f="/Users/spd/.codeindex/$name/$dbfile"
    if [ -e "$f" ]; then
      rm -rf "$f"
      echo "Removed $f"
    fi
  done
  csv="/Users/spd/.codeindex/$name/csv"
  if [ -d "$csv" ]; then
    rm -rf "$csv"
    echo "Removed $csv"
  fi
done

echo ""
echo "=== Starting re-index ==="
echo ""

# Project list: name|repoPath
projects="
CodeIndex|/Users/spd/conductor/repos/codeindex
ParrotKey|/Users/spd/conductor/repos/yobert-v1
ThePhrame|/Users/spd/conductor/repos/thephrame
codeindexweb|/Users/spd/conductor/repos/codeindexweb
domeinchecker|/Users/spd/conductor/repos/domeinchecker
goeiemorgen|/Users/spd/conductor/repos/goeiemorgen2
iphrame|/Users/spd/conductor/workspaces/iphrame/dubai-v2
Sequencer|/Users/spd/conductor/workspaces/siepsequencer/lyon-v2
sequencer-app|/Users/spd/Development/sequencer/sequencer-app
"

total=9
current=0

echo "$projects" | while IFS='|' read -r name rp; do
  [ -z "$name" ] && continue
  current=$((current + 1))

  if [ ! -d "$rp" ]; then
    echo "[$current/$total] SKIP $name (source not found: $rp)"
    echo ""
    continue
  fi

  echo "[$current/$total] Indexing $name from $rp..."
  if codeindex analyze "$name" "$rp" 2>&1 | tail -5; then
    echo "  Done!"
  else
    echo "  FAILED (exit code $?)"
  fi
  echo ""
done

echo "=== Migration complete ==="
echo "Start the app with: bun run dev"
