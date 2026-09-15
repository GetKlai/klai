#!/usr/bin/env bash
# Keep the two shared code-intelligence daemons rooted in a directory that
# cannot be deleted out from under them.
#
# zvec-grep and codebase-memory-mcp each run ONE daemon per machine, shared by
# every repo and every Claude session. Neither chooses its own working
# directory: each inherits the cwd of whichever process first started it. For
# zvec-grep that starter is normally `zg server --stdio`, the MCP bootstrap
# Claude Code spawns inside the workspace it is running in, so the daemon ends
# up rooted in a Conductor workspace. When that workspace is later archived the
# directory is deleted and the daemon keeps running with a cwd that no longer
# exists.
#
# That state is not self-healing and not self-reporting. `zg server status
# --check-ready` still answers "ready" and exits 0, while every index write for
# EVERY workspace on the machine fails with "ENOENT: process.cwd failed ...
# uv_cwd", and `--mode direct` cannot route around it because the daemon still
# holds the write lease for the root (ZVEC_GREP.ENGINE.DAEMON_LEASE_ACTIVE).
# One archived workspace therefore breaks semantic search everywhere, silently.
# Measured on 2026-09-15 by reproducing it on an isolated daemon on port 7997.
#
# There is no upstream cwd health check to enable, so the invariant is enforced
# here instead, and it is deliberately blunt: a shared daemon must run from
# $HOME. $HOME is the one directory on this machine that no archive can remove.
# A daemon that is not running is started from $HOME too, which is the half
# that prevents the problem rather than repairing it: once a healthy daemon is
# listening, a later `zg server --stdio` reuses it and does NOT move its cwd
# (verified: cwd stayed /Users/mvletter across a stdio connect from /tmp).
#
# Usage:
#   code-daemon-guard.sh           ensure the invariant, restarting if needed
#   code-daemon-guard.sh --check   report only, exit 1 if it is violated
set -uo pipefail

check_only=0
[[ ${1:-} == "--check" ]] && check_only=1

violations=0

# Resolve a process's working directory. On macOS lsof still prints the old
# path after the directory is deleted, so an existing-directory test is what
# actually distinguishes a live cwd from a dead one -- the string alone does not.
proc_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
}

# Decide what to do about one daemon. Prints a verdict and returns 0 when the
# invariant already holds, 1 when it does not.
inspect() {
  local label=$1 pid=$2 cwd
  if [[ -z $pid ]]; then
    echo "  $label: not running"
    return 1
  fi
  cwd=$(proc_cwd "$pid")
  if [[ -z $cwd ]]; then
    echo "  $label: pid $pid, working directory unreadable"
    return 1
  fi
  if [[ ! -d $cwd ]]; then
    echo "  $label: pid $pid, working directory DELETED ($cwd)"
    return 1
  fi
  if [[ $cwd != "$HOME" ]]; then
    echo "  $label: pid $pid, rooted outside \$HOME ($cwd)"
    return 1
  fi
  echo "  $label: pid $pid, rooted at \$HOME"
  return 0
}

echo "==> shared code-intelligence daemons"

# --- zvec-grep ---------------------------------------------------------------
if command -v zg >/dev/null 2>&1; then
  zg_pid=$(zg server status 2>/dev/null | sed -n 's/^PID: *//p' | head -1)
  if ! inspect "zvec-grep" "$zg_pid"; then
    violations=$((violations + 1))
    if ((check_only == 0)); then
      echo "     restarting zvec-grep from \$HOME"
      # A daemon with a dead cwd cannot always shut itself down cleanly, so an
      # unsuccessful `off` is not fatal; `on` is the step that has to work.
      (cd "$HOME" && zg server off >/dev/null 2>&1) || true
      if (cd "$HOME" && zg server on >/dev/null 2>&1); then
        inspect "zvec-grep" "$(zg server status 2>/dev/null | sed -n 's/^PID: *//p' | head -1)"
      else
        echo "     FAILED to restart zvec-grep" >&2
      fi
    fi
  fi
else
  echo "  zvec-grep: not installed (see docs/setup/mcp-servers.md Section 5)"
fi

# --- codebase-memory-mcp -----------------------------------------------------
# CLI-only by standing rule: this daemon is the CLI's own warm process, which
# saves ~2.6s of startup per call. It is never an MCP server and never a file
# watcher, but it inherits a cwd exactly like zvec-grep's does.
if command -v codebase-memory-mcp >/dev/null 2>&1; then
  cbm_pid=$(codebase-memory-mcp daemon status 2>/dev/null | sed -n 's/^ *pid: *//p' | head -1)
  if ! inspect "codebase-memory" "$cbm_pid"; then
    violations=$((violations + 1))
    if ((check_only == 0)); then
      echo "     restarting codebase-memory from \$HOME"
      (cd "$HOME" && codebase-memory-mcp daemon stop >/dev/null 2>&1) || true
      if (cd "$HOME" && codebase-memory-mcp daemon start >/dev/null 2>&1); then
        inspect "codebase-memory" "$(codebase-memory-mcp daemon status 2>/dev/null | sed -n 's/^ *pid: *//p' | head -1)"
      else
        echo "     FAILED to restart codebase-memory" >&2
      fi
    fi
  fi
else
  echo "  codebase-memory: not installed (see docs/setup/mcp-servers.md Section 7)"
fi

# Serena has no shared daemon and no listening port: Claude Code spawns one
# stdio subprocess per session and `web_dashboard: false` in
# ~/.serena/serena_config.yml keeps it off 24282. Nothing to guard.

if ((check_only == 1 && violations > 0)); then
  echo "" >&2
  echo "$violations shared daemon(s) violate the \$HOME invariant; run scripts/code-daemon-guard.sh" >&2
  exit 1
fi

exit 0
