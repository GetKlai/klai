#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

failed=0
active_paths=(
  AGENTS.md
  CLAUDE.md
  .agents/skills
  .claude/agents
  .claude/commands
  .claude/rules
  .claude/settings.json
  klai-website/AGENTS.md
  klai-website/CLAUDE.md
  klai-website/.claude/agents
  klai-website/.claude/commands
  klai-website/.claude/settings.json
)

existing_paths=()
for path in "${active_paths[@]}"; do
  [[ -e "$path" ]] && existing_paths+=("$path")
done

# Regex is intentionally literal; shell expansion would corrupt backticks and
# character classes.
# shellcheck disable=SC2016
forbidden='\.moai|klai-claude|/Users/|klai-focus|research-api|(^|[/`[:space:]])\.shared([/`[:space:]]|$)'
if ((${#existing_paths[@]})) && rg -n "$forbidden" -- "${existing_paths[@]}"; then
  echo "ERROR: active agent configuration contains a retired path reference" >&2
  failed=1
fi

legacy_pitfall_refs=$(rg -n 'process-rules\.md' \
  .agents .claude/agents .claude/commands .claude/rules .claude/skills \
  --glob '!**/pitfalls/process-rules.md' 2>/dev/null || true)
if [[ -n "$legacy_pitfall_refs" ]]; then
  printf '%s\n' "$legacy_pitfall_refs"
  echo "ERROR: active agent configuration points at the compatibility index instead of a maintained rule" >&2
  failed=1
fi

max_unscoped_lines=200
while IFS= read -r -d '' rule; do
  # A frontmatter block is scoped only when it contains a concrete `paths:`
  # entry. Catch rules with no paths and catch effectively global `**` paths.
  if awk '
    NR == 1 && $0 == "---" { in_frontmatter = 1; next }
    in_frontmatter && $0 == "---" { exit }
    in_frontmatter && /^[[:space:]]*paths:[[:space:]]*/ {
      has_paths = 1
      if ($0 ~ /^[[:space:]]*paths:[[:space:]]*\[[[:space:]]*["'"'"']?\*\*["'"'"']?[[:space:]]*\][[:space:]]*$/) {
        global_path = 1
      }
      next
    }
    in_frontmatter && has_paths && /^[[:space:]]*-[[:space:]]*["'"'"']?\*\*["'"'"']?[[:space:]]*$/ {
      global_path = 1
    }
    END { exit !(has_paths && !global_path) }
  ' "$rule"; then
    continue
  fi
  line_count=$(wc -l < "$rule")
  if ((line_count > max_unscoped_lines)); then
    echo "ERROR: unscoped rule exceeds ${max_unscoped_lines} lines: ${rule} (${line_count})" >&2
    failed=1
  fi
done < <(find .claude/rules -type f -name '*.md' -print0)

if ((failed)); then
  exit 1
fi

echo "Agent knowledge governance checks passed"
