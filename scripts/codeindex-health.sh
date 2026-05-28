#!/usr/bin/env bash
# Diagnose and repair common CodeIndex stale-index failures.
#
# Usage:
#   scripts/codeindex-health.sh
#   scripts/codeindex-health.sh --repair
#   scripts/codeindex-health.sh --repair --restart-mcp
#
# Why this exists:
# CodeIndex keeps one canonical index per repo. In Conductor worktrees, the
# current workspace can differ from the canonical repo path stored by CodeIndex.
# Long-lived `codeindex serve` or stale `codeindex mcp` processes can also keep
# cached state around, making every agent see the same stale warning.

set -u

REPAIR=0
RESTART_MCP=0
QUIET=0
PROJECT_NAME="${CODEINDEX_PROJECT_NAME:-klai}"

for arg in "$@"; do
  case "$arg" in
    --repair)
      REPAIR=1
      ;;
    --restart-mcp)
      RESTART_MCP=1
      ;;
    --quiet)
      QUIET=1
      ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

log() {
  if [ "$QUIET" -eq 0 ]; then
    printf '%s\n' "$*"
  fi
}

warn() {
  printf '%s\n' "$*" >&2
}

require_codeindex() {
  if ! command -v codeindex >/dev/null 2>&1; then
    warn "ERROR: codeindex is not on PATH"
    exit 1
  fi
}

status_output() {
  codeindex status 2>&1
}

canonical_repo_from_status() {
  sed -n 's/^Repository: //p' | head -1
}

indexed_commit_from_status() {
  sed -n 's/^Indexed commit: //p' | awk '{print $1}' | head -1
}

is_up_to_date() {
  grep -q 'Status: .*up-to-date'
}

is_locked() {
  grep -qi 'database is locked\|locked by another process'
}

stop_codeindex_serve() {
  local pids
  pids="$(pgrep -f 'codeindex serve' || true)"
  if [ -z "$pids" ]; then
    log "No codeindex serve process to stop."
    return 0
  fi

  log "Stopping codeindex serve process(es): ${pids//$'\n'/ }"
  # `serve` is a local web UI/cache process. It is safe to restart later and
  # has caused stale DB locks when left running across many Conductor sessions.
  pkill -f 'codeindex serve' || true
  sleep 1
}

restart_mcp_processes() {
  local pids
  pids="$(pgrep -f 'codeindex mcp' || true)"
  if [ -z "$pids" ]; then
    log "No codeindex mcp process to restart."
    return 0
  fi

  log "Stopping codeindex mcp process(es): ${pids//$'\n'/ }"
  pkill -f 'codeindex mcp' || true
  sleep 1
}

run_analyze_from() {
  local worktree_dir="$1"
  if [ ! -d "$worktree_dir/.git" ] && [ ! -f "$worktree_dir/.git" ]; then
    warn "ERROR: CodeIndex worktree path is not a git checkout: $worktree_dir"
    return 1
  fi

  log "Running codeindex analyze from current worktree: $worktree_dir"
  (cd "$worktree_dir" && codeindex analyze "$PROJECT_NAME" "$worktree_dir" --force --no-embeddings)
}

main() {
  require_codeindex

  local status repo_dir worktree_dir current_head indexed_commit
  status="$(status_output)"
  repo_dir="$(printf '%s\n' "$status" | canonical_repo_from_status)"
  worktree_dir="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  current_head="$(git -C "$worktree_dir" rev-parse HEAD 2>/dev/null || true)"
  indexed_commit="$(printf '%s\n' "$status" | indexed_commit_from_status)"

  if [ -z "$repo_dir" ]; then
    warn "$status"
    warn "ERROR: could not determine CodeIndex canonical repository path"
    exit 1
  fi

  log "$status"

  if printf '%s\n' "$status" | is_up_to_date; then
    if [ "$RESTART_MCP" -eq 1 ]; then
      restart_mcp_processes
    fi
    exit 0
  fi

  # CodeIndex status currently compares a Conductor worktree index against the
  # canonical checkout's HEAD because both share the same git common-dir. If the
  # stored index commit matches this worktree's HEAD, the index is healthy for
  # this workspace even when `codeindex status` prints a stale warning.
  if [ -n "$current_head" ] && [ -n "$indexed_commit" ]; then
    case "$current_head" in
      "$indexed_commit"*)
        log "CodeIndex indexed commit matches current worktree ($indexed_commit); treating as healthy."
        if [ "$RESTART_MCP" -eq 1 ]; then
          restart_mcp_processes
        fi
        exit 0
        ;;
    esac
  fi

  if [ "$REPAIR" -ne 1 ]; then
    warn ""
    warn "CodeIndex is not up to date. Run:"
    warn "  scripts/codeindex-health.sh --repair"
    warn ""
    warn "If existing agents still show stale context after repair, run:"
    warn "  scripts/codeindex-health.sh --repair --restart-mcp"
    exit 1
  fi

  # A stale or locked index is usually caused by an old web UI server. Stop it
  # before updating. Do not stop MCP by default: this script is also used by the
  # MCP launcher, and killing the current stdio process would break startup.
  stop_codeindex_serve

  local update_log
  update_log="$(run_analyze_from "$worktree_dir" 2>&1)"
  local update_rc=$?
  log "$update_log"

  if [ "$update_rc" -ne 0 ] && printf '%s\n' "$update_log" | is_locked; then
    warn "CodeIndex analyze hit a DB lock; stopping codeindex serve and retrying once."
    stop_codeindex_serve
    update_log="$(run_analyze_from "$worktree_dir" 2>&1)"
    update_rc=$?
    log "$update_log"
  fi

  if [ "$update_rc" -ne 0 ]; then
    warn "$update_log"
    exit "$update_rc"
  fi

  status="$(status_output)"
  log "$status"
  indexed_commit="$(printf '%s\n' "$status" | indexed_commit_from_status)"

  if printf '%s\n' "$status" | is_up_to_date; then
    :
  elif [ -n "$current_head" ] && [ -n "$indexed_commit" ]; then
    case "$current_head" in
      "$indexed_commit"*)
        log "CodeIndex indexed commit matches current worktree ($indexed_commit); repair succeeded."
        ;;
      *)
        warn "ERROR: CodeIndex still reports stale after repair."
        warn "$status"
        exit 1
        ;;
    esac
  else
    warn "ERROR: CodeIndex still reports stale after repair."
    warn "$status"
    exit 1
  fi

  if [ "$RESTART_MCP" -eq 1 ]; then
    restart_mcp_processes
  fi
}

main
