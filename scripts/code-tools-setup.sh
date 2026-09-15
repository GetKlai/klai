#!/usr/bin/env bash
# Build the per-worktree state the three code-intelligence tools need.
#
# All three keep their index outside git and keyed by working directory, so a
# fresh Conductor worktree starts with nothing and the tools answer "no index"
# instead of failing loudly. Conductor runs this on workspace creation
# (.conductor/settings.toml); run it by hand with `make code-tools` in a plain
# clone, after a merge that moved a lot of code, or when a tool says its index
# is missing or stale.
#
# A tool that is not installed is skipped with a pointer to its install step.
# A tool that IS installed and then fails is a failure, because that is the
# case where an agent would otherwise silently fall back to grep.
set -u

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

failed=()
skipped=()

step() {
  printf '\n==> %s\n' "$1"
}

# Both shared daemons inherit the cwd of whoever starts them, so this has to
# run before anything below touches them: it starts each one from $HOME if it
# is missing, which stops a later `zg server --stdio` inside this worktree from
# becoming the starter and rooting the machine-wide daemon in a directory
# Conductor can archive. See the header of that script for the failure it
# prevents.
scripts/code-daemon-guard.sh || failed+=("code-daemon-guard")

# --- zvec-grep: semantic search index at the worktree root -------------------
step "zvec-grep index"
if ! command -v zg >/dev/null 2>&1; then
  skipped+=("zvec-grep (zg not on PATH — see docs/setup/mcp-servers.md Section 5)")
else
  # zg 0.2.2 already picks this model for a new index with no config file
  # present, so this pins the choice rather than fixing a failure: a future
  # default that is remote would otherwise start asking for an API key, or
  # embed one worktree differently from the next. The var applies to new
  # indexes only, so it cannot re-embed an existing one. This model runs
  # on-device: no API key, no network.
  export ZVEC_GREP_EMBEDDING="${ZVEC_GREP_EMBEDDING:-local/potion-code-16m-v2}"
  # code-daemon-guard.sh above has already guaranteed a daemon rooted at $HOME,
  # so an index failure here is a real failure and not the archived-workspace
  # trap. Let it fail.
  if ! zg index .; then
    failed+=("zvec-grep")
  fi
  zg status . | sed -n '1,4p'
fi

# --- Serena: LSP symbol cache ------------------------------------------------
step "Serena symbol cache"
if ! command -v serena >/dev/null 2>&1; then
  skipped+=("Serena (serena not on PATH — see docs/setup/mcp-servers.md Section 1)")
else
  # Warm-up only: Serena's MCP tools work without this, they are just slow on
  # the first symbol lookup of each file. The cache lands in .serena/cache,
  # which .serena/.gitignore already excludes.
  if serena project index . >/dev/null 2>&1; then
    echo "serena: symbol cache written to .serena/cache"
  else
    failed+=("Serena")
  fi
fi

# --- codebase-memory-mcp: code graph (CLI only, never an MCP server) ---------
step "codebase-memory-mcp graph"
if ! command -v codebase-memory-mcp >/dev/null 2>&1; then
  skipped+=("codebase-memory-mcp (not on PATH — see docs/setup/mcp-servers.md Section 7)")
else
  if ! codebase-memory-mcp cli index_repository --repo-path . >/dev/null 2>&1; then
    failed+=("codebase-memory-mcp")
  fi
  # The warm daemon that saves ~2.6s of startup per CLI call (measured: 3.9s
  # cold vs 1.35s warm for the same get_architecture call) is started by
  # code-daemon-guard.sh, from $HOME. Starting it here would root it in this
  # worktree and recreate the archived-workspace failure in the second tool.
  echo "project name for --project: $(pwd -P | sed 's#^/##; s#/#-#g')"
fi

# --- Summary -----------------------------------------------------------------
printf '\n'
if ((${#skipped[@]})); then
  printf 'skipped: %s\n' "${skipped[@]}"
fi
if ((${#failed[@]})); then
  printf 'FAILED: %s\n' "${failed[@]}" >&2
  exit 1
fi
echo "code-intelligence tools ready for $(pwd -P)"
