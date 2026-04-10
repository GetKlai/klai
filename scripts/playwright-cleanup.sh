#!/bin/bash
# Clean up stale Brave/Chromium lock files for Playwright MCP.
# Run before MCP start to prevent "Browser is already in use" errors.
PROFILE="/Users/mark/.claude/mcp-brave-profile"

if [ ! -e "$PROFILE/SingletonLock" ]; then
  exit 0
fi

PID=$(readlink "$PROFILE/SingletonLock" 2>/dev/null | sed 's/.*-//')
if [ -z "$PID" ]; then
  rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket"
  exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
  rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket"
  echo "Removed stale lock for dead PID $PID"
else
  pkill -f "Brave.*mcp-brave-profile" 2>/dev/null
  sleep 1
  rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonSocket"
  echo "Killed Brave PID $PID and removed locks"
fi
