#!/bin/bash
# CodeIndex macOS Installer — Preflight Check
# Verifies Node.js 18+ is installed before proceeding.

REAL_USER=$(stat -f '%Su' /dev/console)
REAL_HOME=$(dscl . -read /Users/"$REAL_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
REAL_HOME="${REAL_HOME:-/Users/$REAL_USER}"

# Search well-known Node.js locations (pkg scripts have minimal PATH)
NODE_CANDIDATES=(
  /usr/local/bin/node
  /opt/homebrew/bin/node
  "$REAL_HOME/.nvm/versions/node"/*/bin/node
  "$REAL_HOME/.volta/bin/node"
  "$REAL_HOME/.fnm/node-versions"/*/installation/bin/node
  "$REAL_HOME/.local/share/fnm/node-versions"/*/installation/bin/node
)

NODE_BIN=""
for candidate in "${NODE_CANDIDATES[@]}"; do
  for resolved in $candidate; do
    if [ -x "$resolved" ]; then
      NODE_BIN="$resolved"
      break 2
    fi
  done
done

# Also check PATH
if [ -z "$NODE_BIN" ] && command -v node &>/dev/null; then
  NODE_BIN=$(command -v node)
fi

if [ -z "$NODE_BIN" ]; then
  osascript -e '
    display dialog "CodeIndex requires Node.js 18 or later.\n\nPlease install Node.js from nodejs.org and try again." \
      buttons {"Download Node.js", "Cancel"} \
      default button 1 \
      with title "CodeIndex Installer" \
      with icon caution
  ' -e '
    if button returned of result is "Download Node.js" then
      open location "https://nodejs.org"
    end if
  ' 2>/dev/null
  exit 1
fi

# Check version >= 18
NODE_VERSION=$("$NODE_BIN" -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VERSION" -lt 18 ] 2>/dev/null; then
  FOUND=$("$NODE_BIN" -v)
  osascript -e "
    display dialog \"CodeIndex requires Node.js 18 or later.\n\nFound: ${FOUND}\nPlease update from nodejs.org.\" \
      buttons {\"Download Node.js\", \"Cancel\"} \
      default button 1 \
      with title \"CodeIndex Installer\" \
      with icon caution
  " -e '
    if button returned of result is "Download Node.js" then
      open location "https://nodejs.org"
    end if
  ' 2>/dev/null
  exit 1
fi

exit 0
