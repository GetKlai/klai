#!/usr/bin/env bash
# Stop-gate: fast repo check. Exit 2 blocks the agent from claiming done.
# No Docker skip exists: every reachable check is a host-local uv/npm lint command.
set -u

cd "$(dirname "$0")/.." || exit 0

base_ref=${CHECK_BASE_REF:-origin/main}
if ! git rev-parse --verify "$base_ref" >/dev/null 2>&1; then
  base_ref=HEAD
fi

changed_paths=$(
  {
    git diff --name-only "$base_ref"...HEAD
    git diff --cached --name-only
    git diff --name-only
    git ls-files --others --exclude-standard
  } | sort -u
)
changed_roots=$(printf '%s\n' "$changed_paths" | awk -F/ '$1 ~ /^klai-/ && NF > 1 { print $1 }' | sort -u)
manifest_dirs=$(
  find klai-* -type f \( -name pyproject.toml -o -name package.json \) \
    -not -path '*/.next/*' \
    -not -path '*/.venv/*' \
    -not -path '*/node_modules/*' \
    -print 2>/dev/null \
    | sed 's#/[^/]*$##' \
    | sort -u
)

checked=()
unchecked=()
not_applicable=()
matched_roots=""

run_check() {
  local label=$1
  shift
  local out

  echo "check gate: linting $label"
  out=$("$@" 2>&1) || {
    echo "check gate command failed ($label):" >&2
    echo "$out" | tail -20 >&2
    exit 2
  }
  checked+=("$label")
}

section_has_dev() {
  local section=$1
  local file=$2

  awk -v section="$section" '
    $0 == section { in_section=1; next }
    /^\[/ { in_section=0 }
    in_section && /^dev[[:space:]]*=/ { found=1 }
    END { exit !found }
  ' "$file"
}

run_ruff_lint() {
  local service=$1
  local config="$service/pyproject.toml"

  if section_has_dev '[dependency-groups]' "$config"; then
    run_check "$service (ruff)" bash -c "cd \"$service\" && uv run --group dev ruff check ."
  elif section_has_dev '[project.optional-dependencies]' "$config"; then
    run_check "$service (ruff)" bash -c "cd \"$service\" && uv run --extra dev ruff check ."
  else
    run_check "$service (ruff)" bash -c "cd \"$service\" && uv run --with ruff ruff check ."
  fi
}

while IFS= read -r service; do
  [ -n "$service" ] || continue
  if ! printf '%s\n' "$changed_paths" | awk -v prefix="$service/" '
    index($0, prefix) == 1 { found=1 }
    END { exit !found }
  '; then
    continue
  fi

  root=${service%%/*}
  matched_roots=$(printf '%s\n%s\n' "$matched_roots" "$root" | sort -u)

  if [ -f "$service/pyproject.toml" ] && grep -q '^\[tool\.ruff\]' "$service/pyproject.toml"; then
    if printf '%s\n' "$changed_paths" | awk -v prefix="$service/" -v config="$service/pyproject.toml" '
      $0 == config || (index($0, prefix) == 1 && $0 ~ /\.py$/) { found=1 }
      END { exit !found }
    '; then
      run_ruff_lint "$service"
    else
      not_applicable+=("$service (no Python/config changes)")
    fi
  elif [ -f "$service/package.json" ] && command -v node >/dev/null 2>&1 \
    && node -e 'const p=require(`./${process.argv[1]}/package.json`); process.exit(p.scripts?.lint ? 0 : 1)' "$service"; then
    if printf '%s\n' "$changed_paths" | awk -v prefix="$service/" '
      index($0, prefix) == 1 && $0 !~ /\.md$/ { found=1 }
      END { exit !found }
    '; then
      run_check "$service (npm lint)" npm --prefix "$service" run lint
    else
      not_applicable+=("$service (documentation-only changes)")
    fi
  else
    unchecked+=("$service (manifest found, but no lint command/config detected)")
  fi
done <<< "$manifest_dirs"

while IFS= read -r root; do
  [ -n "$root" ] || continue
  if ! printf '%s\n' "$matched_roots" | grep -Fqx "$root"; then
    unchecked+=("$root (no manifest-backed lint target matched the changed paths)")
  fi
done <<< "$changed_roots"

if [ ${#checked[@]} -eq 0 ] && [ ${#not_applicable[@]} -eq 0 ]; then
  echo "check gate fallback: no changed manifest-backed lint command; checking portal backend + frontend only"
  run_check "portal fallback (make lint; other services not checked)" make lint
fi

echo "check gate checked: ${checked[*]}"
if [ ${#unchecked[@]} -gt 0 ]; then
  echo "check gate not checked: ${unchecked[*]}"
else
  echo "check gate not checked: none of the changed service paths"
fi
if [ ${#not_applicable[@]} -gt 0 ]; then
  echo "check gate not applicable: ${not_applicable[*]}"
fi
