#!/bin/bash
# CodeIndex macOS Installer — Post-install
# Installs CLI globally, copies web assets, runs setup.
set -e

PAYLOAD="/tmp/codeindex-install"
REAL_USER=$(stat -f '%Su' /dev/console)
REAL_HOME=$(dscl . -read /Users/"$REAL_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
REAL_HOME="${REAL_HOME:-/Users/$REAL_USER}"

# ── Find npm in well-known locations ──────────────────────────────
find_bin() {
  local name="$1"
  local candidates=(
    "/usr/local/bin/$name"
    "/opt/homebrew/bin/$name"
    "$REAL_HOME/.nvm/versions/node"/*/bin/"$name"
    "$REAL_HOME/.volta/bin/$name"
    "$REAL_HOME/.fnm/node-versions"/*/installation/bin/"$name"
    "$REAL_HOME/.local/share/fnm/node-versions"/*/installation/bin/"$name"
  )
  for candidate in "${candidates[@]}"; do
    for resolved in $candidate; do
      if [ -x "$resolved" ]; then
        echo "$resolved"
        return
      fi
    done
  done
  command -v "$name" 2>/dev/null
}

NPM_BIN=$(find_bin npm)
if [ -z "$NPM_BIN" ]; then
  echo "ERROR: npm not found"
  exit 1
fi

# Ensure node/npm dir is in PATH for subprocesses
export PATH="$(dirname "$NPM_BIN"):$PATH"

# ── 1. Install codeindex CLI globally ─────────────────────────────
TGZ="$PAYLOAD/codeindex.tgz"
if [ ! -f "$TGZ" ]; then
  echo "ERROR: codeindex.tgz not found in payload"
  exit 1
fi

NPM_PREFIX=$(sudo -u "$REAL_USER" "$NPM_BIN" prefix -g 2>/dev/null || true)

if [ -n "$NPM_PREFIX" ] && sudo -u "$REAL_USER" test -w "$NPM_PREFIX" 2>/dev/null; then
  sudo -u "$REAL_USER" "$NPM_BIN" install -g "$TGZ"
else
  "$NPM_BIN" install -g "$TGZ"
fi

# ── 2. Install web UI to ~/.codeindex/web/ ────────────────────────
WEB_DIR="$REAL_HOME/.codeindex/web"
if [ -d "$PAYLOAD/web-dist" ]; then
  mkdir -p "$WEB_DIR"
  rm -rf "$WEB_DIR"/*
  cp -R "$PAYLOAD/web-dist/"* "$WEB_DIR/"
  chown -R "$REAL_USER" "$REAL_HOME/.codeindex"
fi

# ── 3. Run codeindex setup as the real user ───────────────────────
CODEINDEX_BIN=$(find_bin codeindex)
if [ -n "$CODEINDEX_BIN" ]; then
  sudo -u "$REAL_USER" "$CODEINDEX_BIN" setup 2>/dev/null || true
fi

# ── 4. Cleanup ────────────────────────────────────────────────────
rm -rf "$PAYLOAD"

exit 0
