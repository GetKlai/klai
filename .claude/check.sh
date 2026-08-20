#!/usr/bin/env bash
# Stop-gate: fast repo check. Exit 2 blocks the agent from claiming done.
# No Docker skip exists: these host-local lint commands do not require a running container.
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
changed_services=$(printf '%s\n' "$changed_paths" | awk -F/ '$1 ~ /^klai-/ && NF > 1 { print $1 }' | sort -u)

checked=()
unchecked=()

run_lint() {
  local label=$1
  shift
  local out

  echo "check gate: linting $label"
  out=$("$@" 2>&1) || {
    echo "check gate failed ($label):" >&2
    echo "$out" | tail -20 >&2
    exit 2
  }
  checked+=("$label")
}

run_ruff_lint() {
  local service=$1

  if grep -q '^\[dependency-groups\]' "$service/pyproject.toml"; then
    run_lint "$service (ruff)" bash -c "cd \"$service\" && uv run --group dev ruff check ."
  elif grep -q '^\[project.optional-dependencies\]' "$service/pyproject.toml"; then
    run_lint "$service (ruff)" bash -c "cd \"$service\" && uv run --extra dev ruff check ."
  else
    run_lint "$service (ruff)" bash -c "cd \"$service\" && uv run ruff check ."
  fi
}

while IFS= read -r service; do
  [ -n "$service" ] || continue

  if [ "$service" = "klai-portal" ]; then
    portal_checked=false
    if printf '%s\n' "$changed_paths" | grep -q '^klai-portal/backend/'; then
      run_lint "klai-portal/backend (ruff)" bash -c 'cd klai-portal/backend && uv run ruff check .'
      portal_checked=true
    fi
    if printf '%s\n' "$changed_paths" | grep -q '^klai-portal/frontend/'; then
      run_lint "klai-portal/frontend (npm lint)" npm --prefix klai-portal/frontend run lint
      portal_checked=true
    fi
    if [ "$portal_checked" = false ]; then
      unchecked+=("klai-portal (no changed lintable backend/frontend path)")
    fi
  elif [ -f "$service/Makefile" ] && awk -F: '$1 == "lint" { found=1 } END { exit !found }' "$service/Makefile"; then
    run_lint "$service (make lint)" make -C "$service" lint
  elif [ -f "$service/pyproject.toml" ] && grep -q '^\[tool\.ruff\]' "$service/pyproject.toml"; then
    run_ruff_lint "$service"
  elif [ -f "$service/package.json" ] && node -e 'const p=require(`./${process.argv[1]}/package.json`); process.exit(p.scripts?.lint ? 0 : 1)' "$service"; then
    run_lint "$service (npm lint)" npm --prefix "$service" run lint
  else
    unchecked+=("$service (no lint target/config detected)")
  fi
done <<< "$changed_services"

if [ ${#checked[@]} -eq 0 ]; then
  echo "check gate fallback: no changed service with a lint target/config; checking portal backend + frontend only"
  run_lint "portal fallback (make lint; other services not checked)" make lint
fi

echo "check gate checked: ${checked[*]}"
if [ ${#unchecked[@]} -gt 0 ]; then
  echo "check gate not checked: ${unchecked[*]}"
else
  echo "check gate not checked: none of the changed lintable services"
fi
