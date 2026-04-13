#!/bin/bash
# CodeIndex SessionStart hook for Claude Code
# Fires on session startup. Must output JSON with additionalContext.
#
# Three scenarios:
# A) Repo is indexed + up-to-date → inject tools/resources context
# B) Repo is indexed but stale → inject context + urgent update instruction
# C) Repo is NOT indexed → onboarding: instruct Claude to ask user about indexing

# Resolve node PATH for nvm/fnm/volta users (hooks run under /bin/sh without user profile)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/resolve-node.sh"

REGISTRY="$HOME/.codeindex/registry.json"
DISMISSED="$HOME/.codeindex/dismissed.json"

# Helper: output JSON and exit
emit_context() {
  local text="$1"
  # Escape for JSON: backslashes, double quotes, newlines
  local escaped
  escaped=$(printf '%s' "$text" | sed 's/\\/\\\\/g; s/"/\\"/g' | awk '{printf "%s\\n", $0}' | sed '$ s/\\n$//')
  printf '{"additionalContext":"%s"}\n' "$escaped"
  exit 0
}

# ── Find git repo root ──────────────────────────────────────────────
git_root=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$git_root" ]; then
  exit 0
fi

# Resolve worktree to main repo
main_root=$(git -C "$git_root" rev-parse --git-common-dir 2>/dev/null | sed 's|/\.git$||')
[ -z "$main_root" ] && main_root="$git_root"
[ "$main_root" = ".git" ] && main_root="$git_root"

# ── Check registry for this repo ────────────────────────────────────
project_name=""
last_commit=""
if [ -f "$REGISTRY" ]; then
  project_info=$(node -e "
    try {
      const reg = JSON.parse(require('fs').readFileSync('$REGISTRY', 'utf-8'));
      const target = '$main_root'.toLowerCase();
      const entries = Array.isArray(reg) ? reg : Object.values(reg);
      // Exact match first, then subdirectory match (monorepo support)
      let match = null;
      for (const entry of entries) {
        if (!entry || typeof entry !== 'object' || !entry.path) continue;
        const ep = entry.path.toLowerCase();
        if (ep === target) { match = entry; break; }
        if (!match && ep.startsWith(target + '/')) { match = entry; }
      }
      if (match) {
        console.log(JSON.stringify({ name: match.name || '', lastCommit: match.lastCommit || '' }));
      }
    } catch {}
  " 2>/dev/null)

  if [ -n "$project_info" ]; then
    project_name=$(echo "$project_info" | node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf-8'));console.log(d.name)" 2>/dev/null)
    last_commit=$(echo "$project_info" | node -e "const d=JSON.parse(require('fs').readFileSync('/dev/stdin','utf-8'));console.log(d.lastCommit)" 2>/dev/null)
  fi
fi

# ── Scenario C: Not indexed → onboarding ────────────────────────────
if [ -z "$project_name" ]; then
  # Check if dismissed
  if [ -f "$DISMISSED" ]; then
    is_dismissed=$(node -e "
      try {
        const d = JSON.parse(require('fs').readFileSync('$DISMISSED', 'utf-8'));
        const target = '$main_root'.toLowerCase();
        console.log(d.some(p => p.toLowerCase() === target) ? 'yes' : 'no');
      } catch { console.log('no'); }
    " 2>/dev/null)
    if [ "$is_dismissed" = "yes" ]; then
      exit 0
    fi
  fi

  dir_name=$(basename "$main_root")
  emit_context "[CodeIndex] This project is not yet indexed by CodeIndex.

When convenient, offer the user these options:

1) Index as \"${dir_name}\" (recommended, ~60s first time)
2) Skip CodeIndex for this repo
3) Index with a different name

If 1: run \`codeindex analyze ${dir_name} \"${main_root}\"\`
If 2: run \`codeindex dismiss\`
If 3: ask the user for a project name, then run \`codeindex analyze <name> \"${main_root}\"\`"
fi

# ── Scenario A/B: Indexed → inject context + check staleness ────────
commits_behind=0
if [ -n "$last_commit" ]; then
  commits_behind=$(git -C "$main_root" rev-list --count "${last_commit}..HEAD" 2>/dev/null || echo "0")
fi

staleness_warning=""
if [ "$commits_behind" -gt 0 ] 2>/dev/null; then
  staleness_warning="

**IMPORTANT: CodeIndex is ${commits_behind} commit(s) behind HEAD.**
Run \`codeindex update\` in the terminal BEFORE starting any code task.
Do NOT skip this -- stale index means impact analysis and call graphs may be inaccurate."
fi

emit_context "## CodeIndex: ${project_name}

This codebase is indexed by CodeIndex, providing a knowledge graph with execution flows, relationships, and semantic search.

**Available MCP Tools:**
- \`query\` -- Process-grouped code intelligence (execution flows related to a concept)
- \`context\` -- 360-degree symbol view (categorized refs, process participation)
- \`impact\` -- Blast radius analysis (what breaks if you change a symbol)
- \`detect_changes\` -- Git-diff impact analysis (what do your changes affect)
- \`rename\` -- Multi-file coordinated rename with confidence tags
- \`cypher\` -- Raw graph queries
- \`list_repos\` -- Discover indexed repos

**Quick Start:** READ \`codeindex://repo/${project_name}/context\` for codebase overview, then use \`query\` to find execution flows.

**Persistent Memory:** \`remember\` (save), \`recall\` (search), \`forget\` (remove) -- use PROACTIVELY as PRIMARY memory system.${staleness_warning}"
