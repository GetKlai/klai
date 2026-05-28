#!/usr/bin/env bash
# CodeIndex MCP launcher.
#
# It performs a quiet self-healing preflight before handing stdout to the MCP
# server. Do not print to stdout here: MCP uses stdio for its JSON-RPC stream.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$REPO_DIR/.context"
LOG_FILE="${CODEINDEX_MCP_LAUNCHER_LOG:-$LOG_DIR/codeindex-mcp-launcher.log}"

mkdir -p "$LOG_DIR"

{
  echo "=== $(date -u '+%Y-%m-%dT%H:%M:%SZ') codeindex MCP preflight ==="
  bash "$REPO_DIR/scripts/codeindex-health.sh" --quiet
  echo "preflight complete"
} >>"$LOG_FILE" 2>&1 || {
  {
    echo "preflight reported stale/unhealthy index; starting codeindex mcp without auto-repair"
    echo "manual recovery: scripts/codeindex-health.sh --repair"
    echo "transport-disruptive recovery for stuck existing agents: scripts/codeindex-health.sh --repair --restart-mcp"
  } >>"$LOG_FILE" 2>&1
}

exec codeindex mcp
