#!/bin/bash
set -e

echo ""
echo "  CodeIndex Installer"
echo "  ==================="
echo ""

# Check Node.js
if ! command -v node &> /dev/null; then
  echo "  Node.js is not installed."
  echo "  Install it from: https://nodejs.org (v18+)"
  exit 1
fi

NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VERSION" -lt 18 ]; then
  echo "  Node.js v18+ required (found: $(node -v))"
  exit 1
fi

echo "  Node.js $(node -v) found"

# Find the tgz in the same directory as this script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TGZ="$SCRIPT_DIR/codeindex.tgz"

if [ ! -f "$TGZ" ]; then
  echo "  codeindex.tgz not found next to this script."
  echo "  Make sure both files are in the same directory."
  exit 1
fi

# Detect if npm global dir is user-writable (nvm/volta/fnm) or system-level
NPM_GLOBAL=$(npm prefix -g 2>/dev/null)
echo "  npm global: $NPM_GLOBAL"

if [ -w "$NPM_GLOBAL" ]; then
  echo "  Installing codeindex (user-level)..."
  npm install -g "$TGZ"
else
  echo "  Installing codeindex (system-level, needs sudo)..."
  sudo npm install -g "$TGZ"
fi

# ── nvm multi-version fix ─────────────────────────────────────────
# When nvm is active, the script's bash subprocess may resolve to a
# different Node version than the user's shell. If `codeindex` already
# exists at a different prefix, install there too.
INSTALL_BIN="$(npm prefix -g)/bin/codeindex"
ACTIVE_BIN="$(command -v codeindex 2>/dev/null || true)"

if [ -n "$ACTIVE_BIN" ] && [ "$INSTALL_BIN" != "$ACTIVE_BIN" ]; then
  ACTIVE_PREFIX="$(dirname "$(dirname "$ACTIVE_BIN")")"
  if [ -x "$ACTIVE_PREFIX/bin/npm" ]; then
    echo ""
    echo "  Updating existing installation at $ACTIVE_PREFIX..."
    "$ACTIVE_PREFIX/bin/npm" install -g "$TGZ"
  fi
fi

# ── Verify installation ──────────────────────────────────────────
EXPECTED=$(tar xzf "$TGZ" -O package/package.json 2>/dev/null | grep '"version"' | head -1 | sed 's/.*: "//;s/".*//')
INSTALLED=$(codeindex --version 2>/dev/null || echo "not found")
if [ "$INSTALLED" != "$EXPECTED" ]; then
  echo ""
  echo "  WARNING: codeindex --version shows $INSTALLED (expected $EXPECTED)"
  echo "  Your shell resolves 'codeindex' from a different Node installation."
  echo "  Run: which codeindex"
  echo ""
fi

echo ""
echo "  Running setup (MCP, skills, hooks)..."
echo ""
codeindex setup

echo ""
echo "  Done! Restart your editor to activate CodeIndex."
echo ""
