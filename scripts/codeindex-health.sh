#!/usr/bin/env bash
# Diagnose and repair common CodeIndex stale-index failures.
#
# Usage:
#   scripts/codeindex-health.sh
#   scripts/codeindex-health.sh --repair
#   scripts/codeindex-health.sh --repair --restart-mcp
#
# Why this exists:
# CodeIndex keeps one shared index per repo. In Conductor worktrees, many
# agents can have different HEADs at once, so the shared index must be pinned
# to a stable base ref (origin/main by default). Branch/worktree changes are an
# overlay: use CodeIndex for the base graph, then read local git diffs/files.

set -u

REPAIR=0
RESTART_MCP=0
QUIET=0
PROJECT_NAME="${CODEINDEX_PROJECT_NAME:-klai}"
BASE_REF="${CODEINDEX_BASE_REF:-origin/main}"
BASE_WORKTREE="${CODEINDEX_BASE_WORKTREE:-$HOME/.codeindex/_worktrees/${PROJECT_NAME}-main}"
LOCK_DIR="${CODEINDEX_LOCK_DIR:-$HOME/.codeindex/.locks/${PROJECT_NAME}-main.lock}"

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

short_sha() {
  printf '%s' "$1" | cut -c1-8
}

base_head() {
  git rev-parse "$BASE_REF" 2>/dev/null || true
}

acquire_lock() {
  local waited=0
  mkdir -p "$(dirname "$LOCK_DIR")"
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    if [ "$waited" -ge 120 ]; then
      warn "ERROR: timed out waiting for CodeIndex lock: $LOCK_DIR"
      return 1
    fi
    log "Waiting for CodeIndex lock: $LOCK_DIR"
    sleep 2
    waited=$((waited + 2))
  done
  trap 'rm -rf "$LOCK_DIR"' EXIT
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

ensure_base_worktree() {
  local source_dir="$1"
  mkdir -p "$(dirname "$BASE_WORKTREE")"

  if [ ! -d "$BASE_WORKTREE/.git" ] && [ ! -f "$BASE_WORKTREE/.git" ]; then
    log "Creating dedicated CodeIndex base worktree: $BASE_WORKTREE"
    git -C "$source_dir" worktree add --detach "$BASE_WORKTREE" "$BASE_REF"
  fi

  log "Fetching base ref for CodeIndex: $BASE_REF"
  # This is a dedicated throwaway worktree owned by CodeIndex health checks.
  # Generated AGENTS/CLAUDE skill files must not make the shared base drift.
  git -C "$BASE_WORKTREE" reset --hard --quiet
  git -C "$BASE_WORKTREE" clean -fd --quiet
  git -C "$BASE_WORKTREE" fetch origin main --quiet
  git -C "$BASE_WORKTREE" checkout --detach "$BASE_REF" --quiet
}

cleanup_base_worktree() {
  if [ ! -d "$BASE_WORKTREE/.git" ] && [ ! -f "$BASE_WORKTREE/.git" ]; then
    return 0
  fi

  # codeindex analyze regenerates agent instruction files in the analyzed tree.
  # The dedicated base worktree is throwaway, so keep it pinned cleanly to main.
  git -C "$BASE_WORKTREE" reset --hard --quiet
  git -C "$BASE_WORKTREE" clean -fd --quiet
}

run_analyze_from_base() {
  local source_dir="$1"
  ensure_base_worktree "$source_dir" || return 1
  if [ ! -d "$BASE_WORKTREE/.git" ] && [ ! -f "$BASE_WORKTREE/.git" ]; then
    warn "ERROR: CodeIndex base worktree path is not a git checkout: $BASE_WORKTREE"
    return 1
  fi

  log "Running codeindex analyze from shared base worktree: $BASE_WORKTREE"
  local analyze_rc=0
  (cd "$BASE_WORKTREE" && codeindex analyze "$PROJECT_NAME" "$BASE_WORKTREE" --force --no-embeddings) || analyze_rc=$?
  cleanup_base_worktree || return 1
  return "$analyze_rc"
}

main() {
  require_codeindex

  local status repo_dir worktree_dir current_head indexed_commit base_commit
  status="$(status_output)"
  repo_dir="$(printf '%s\n' "$status" | canonical_repo_from_status)"
  worktree_dir="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  current_head="$(git -C "$worktree_dir" rev-parse HEAD 2>/dev/null || true)"
  indexed_commit="$(printf '%s\n' "$status" | indexed_commit_from_status)"
  base_commit="$(base_head)"

  if [ -z "$repo_dir" ]; then
    warn "$status"
    warn "ERROR: could not determine CodeIndex canonical repository path"
    exit 1
  fi

  log "$status"
  if [ -n "$base_commit" ]; then
    log "CodeIndex base ref: $BASE_REF ($(short_sha "$base_commit"))"
  else
    warn "WARNING: could not resolve CodeIndex base ref: $BASE_REF"
  fi

  # Health is defined against the shared base ref, not against whichever
  # Conductor worktree the current agent is using and not against the canonical
  # checkout's possibly-stale HEAD.
  if [ -n "$base_commit" ] && [ -n "$indexed_commit" ]; then
    case "$current_head" in
      "$base_commit"*) ;;
      *) log "Current worktree differs from $BASE_REF; treat local changes as an overlay on the shared CodeIndex graph." ;;
    esac
    case "$base_commit" in
      "$indexed_commit"*|"$indexed_commit")
        log "CodeIndex indexed commit matches $BASE_REF ($(short_sha "$indexed_commit")); treating as healthy."
        if [ "$REPAIR" -eq 1 ]; then
          cleanup_base_worktree || exit 1
        fi
        if [ "$RESTART_MCP" -eq 1 ]; then
          restart_mcp_processes
        fi
        exit 0
        ;;
    esac
  fi

  if [ "$REPAIR" -ne 1 ]; then
    warn ""
    warn "CodeIndex shared base index is not up to date. Run:"
    warn "  scripts/codeindex-health.sh --repair"
    warn ""
    warn "If existing agents are stuck after repair, this intentionally closes their MCP transports:"
    warn "  scripts/codeindex-health.sh --repair --restart-mcp"
    warn "Restart affected sessions after using --restart-mcp."
    exit 1
  fi

  # A stale or locked index is usually caused by an old web UI server. Stop it
  # before updating. Do not stop MCP by default: this script is also used by the
  # MCP launcher, and killing the current stdio process would break startup.
  acquire_lock || exit 1
  stop_codeindex_serve

  local update_log
  update_log="$(run_analyze_from_base "$worktree_dir" 2>&1)"
  local update_rc=$?
  log "$update_log"

  if [ "$update_rc" -ne 0 ] && printf '%s\n' "$update_log" | is_locked; then
    warn "CodeIndex analyze hit a DB lock; stopping codeindex serve and retrying once."
    stop_codeindex_serve
    update_log="$(run_analyze_from_base "$worktree_dir" 2>&1)"
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
  base_commit="$(base_head)"

  if [ -n "$base_commit" ] && [ -n "$indexed_commit" ]; then
    case "$base_commit" in
      "$indexed_commit"*|"$indexed_commit")
        log "CodeIndex indexed commit matches $BASE_REF ($(short_sha "$indexed_commit")); repair succeeded."
        ;;
      *)
        warn "ERROR: CodeIndex still does not match $BASE_REF after repair."
        warn "$status"
        exit 1
        ;;
    esac
  elif printf '%s\n' "$status" | is_up_to_date; then
    :
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
