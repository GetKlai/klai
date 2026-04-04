#!/bin/bash
# Wrapper: runs codeindex analyze + enrichment in one command.
# Usage: ./scripts/codeindex-analyze-and-enrich.sh [--force]
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo ""
echo "  CodeIndex + Enrichment Pipeline"
echo "  ================================"
echo ""

# Phase 1: CodeIndex analyze
if [ "$1" = "--force" ]; then
  codeindex analyze --force
else
  codeindex analyze
fi

# Phase 2: Enrichment
echo ""
echo "  Running enrichment layer..."
echo ""
node "$SCRIPT_DIR/codeindex-enrich.mjs" --repo-path "$REPO_DIR"
