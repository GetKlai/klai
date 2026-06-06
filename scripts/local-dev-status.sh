#!/usr/bin/env bash
# Report the effective Klai local/prod E2E test contract for this checkout.
#
# Use before browser testing. It catches the two expensive mistakes:
# - local UI checks accidentally running through production auth/proxy
# - Conductor workspaces colliding on fixed dev ports

set -euo pipefail

MODE="local"
STRICT=0
QUIET=0
EXPECTED_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --strict)
      STRICT=1
      shift
      ;;
    --quiet)
      QUIET=1
      shift
      ;;
    --expected-url)
      EXPECTED_URL="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
FRONTEND_DIR="$ROOT/klai-portal/frontend"
BACKEND_DIR="$ROOT/klai-portal/backend"
WORKSPACE_NAME="$(basename "$ROOT")"

FRONTEND_PORT="${FRONTEND_PORT:-${CONDUCTOR_PORT:-5174}}"
if [[ -n "${BACKEND_PORT:-}" ]]; then
  BACKEND_PORT="$BACKEND_PORT"
elif [[ -n "${CONDUCTOR_PORT:-}" ]]; then
  BACKEND_PORT="$((CONDUCTOR_PORT + 1))"
else
  BACKEND_PORT="8010"
fi

FAILURES=()
WARNINGS=()

say() {
  [[ "$QUIET" -eq 1 ]] || printf '%s\n' "$1"
}

fail() {
  FAILURES+=("$1")
}

warn() {
  WARNINGS+=("$1")
}

env_file_value() {
  local file="$1"
  local key="$2"
  [[ -f "$file" ]] || return 1
  awk -F= -v key="$key" '
    $0 ~ "^[[:space:]]*(export[[:space:]]+)?" key "=" {
      sub(/^[[:space:]]*export[[:space:]]+/, "", $0)
      sub("^[^=]*=", "", $0)
      gsub(/^['\''"]|['\''"]$/, "", $0)
      print $0
      found=1
      exit
    }
    END { if (!found) exit 1 }
  ' "$file"
}

effective_frontend_env() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
    return 0
  fi

  local file
  for file in \
    "$FRONTEND_DIR/.env.development.local" \
    "$FRONTEND_DIR/.env.local" \
    "$FRONTEND_DIR/.env.development" \
    "$FRONTEND_DIR/.env"; do
    if value="$(env_file_value "$file" "$key" 2>/dev/null)"; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  return 1
}

backend_env() {
  env_file_value "$BACKEND_DIR/.env" "$1" 2>/dev/null || true
}

listener_command() {
  local port="$1"
  local pid
  pid="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
  [[ -n "$pid" ]] || return 1
  ps -p "$pid" -o command= 2>/dev/null || true
}

port_from_url() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
print(u.port or (443 if u.scheme == "https" else 80))
PY
}

check_other_workspace_listener() {
  local label="$1"
  local port="$2"
  local cmd
  cmd="$(listener_command "$port" || true)"
  [[ -n "$cmd" ]] || return 0

  if [[ "$cmd" == *"/conductor/workspaces/"* && "$cmd" != *"$ROOT"* ]]; then
    warn "$label port $port is owned by another workspace: $cmd"
  else
    say "  $label listener on :$port -> $cmd"
  fi
}

mode_local() {
  local auth_dev_mode
  local api_proxy_target
  local backend_auth_dev_mode
  local backend_cmd
  local frontend_cmd
  local expected_port

  auth_dev_mode="$(effective_frontend_env VITE_AUTH_DEV_MODE 2>/dev/null || true)"
  api_proxy_target="$(effective_frontend_env VITE_API_PROXY_TARGET 2>/dev/null || true)"
  backend_auth_dev_mode="$(backend_env AUTH_DEV_MODE)"

  say "Klai local dev status"
  say "  workspace:        $WORKSPACE_NAME"
  say "  root:             $ROOT"
  say "  frontend URL:     http://localhost:$FRONTEND_PORT/"
  say "  backend URL:      http://localhost:$BACKEND_PORT/"
  say "  CONDUCTOR_PORT:   ${CONDUCTOR_PORT:-<unset>}"
  say ""

  if [[ -f "$FRONTEND_DIR/.env.local" ]] && ! env_file_value "$FRONTEND_DIR/.env.local" VITE_AUTH_DEV_MODE >/dev/null 2>&1; then
    warn "frontend/.env.local exists but contains no VITE_AUTH_DEV_MODE; keep prod-E2E secrets there, use .env.development.local for Vite dev"
  fi

  [[ -f "$FRONTEND_DIR/.env.development.local" ]] || fail "missing $FRONTEND_DIR/.env.development.local; run make setup"
  [[ -f "$BACKEND_DIR/.env" ]] || fail "missing $BACKEND_DIR/.env; run make setup"

  if [[ "$auth_dev_mode" != "true" ]]; then
    fail "VITE_AUTH_DEV_MODE is '$auth_dev_mode' (expected true for standalone local UI); otherwise the browser can redirect to production login"
  fi

  if [[ "$api_proxy_target" != "http://localhost:$BACKEND_PORT" && "$api_proxy_target" != "http://127.0.0.1:$BACKEND_PORT" ]]; then
    fail "VITE_API_PROXY_TARGET is '${api_proxy_target:-<unset>}' (expected http://localhost:$BACKEND_PORT for standalone local UI)"
  fi

  if [[ "$backend_auth_dev_mode" != "true" ]]; then
    fail "backend AUTH_DEV_MODE is '${backend_auth_dev_mode:-<unset>}' (expected true for standalone local UI)"
  fi

  if [[ -n "$EXPECTED_URL" ]]; then
    expected_port="$(port_from_url "$EXPECTED_URL")"
    [[ "$expected_port" == "$FRONTEND_PORT" ]] || fail "expected URL uses port $expected_port, but this workspace frontend port is $FRONTEND_PORT"
  fi

  check_other_workspace_listener "frontend" "$FRONTEND_PORT"
  check_other_workspace_listener "backend" "$BACKEND_PORT"

  frontend_cmd="$(listener_command "$FRONTEND_PORT" || true)"
  backend_cmd="$(listener_command "$BACKEND_PORT" || true)"
  if [[ "$STRICT" -eq 1 ]]; then
    [[ -n "$frontend_cmd" ]] || fail "no frontend listener on port $FRONTEND_PORT; start with make frontend"
    [[ -n "$backend_cmd" ]] || fail "no backend listener on port $BACKEND_PORT; start with make backend"
  fi

  if ! docker info >/dev/null 2>&1; then
    warn "Docker/OrbStack is not reachable; make dev-up/migrate cannot work until Docker is running"
  fi

  say ""
  say "Correct local standalone sequence:"
  say "  make setup"
  say "  make dev-up"
  say "  make migrate"
  say "  make backend    # terminal 1"
  say "  make frontend   # terminal 2"
  say "  scripts/local-dev-status.sh --mode local --strict"
}

mode_prod_e2e() {
  local e2e_env="$FRONTEND_DIR/.env.local"

  say "Klai production E2E status"
  say "  workspace:      $WORKSPACE_NAME"
  say "  env file:       $e2e_env"
  say ""

  [[ -f "$e2e_env" ]] || fail "missing $e2e_env with E2E_BASE_URL/E2E_USER_EMAIL/E2E_USER_PASSWORD"
  env_file_value "$e2e_env" E2E_BASE_URL >/dev/null 2>&1 || fail "missing E2E_BASE_URL in $e2e_env"
  env_file_value "$e2e_env" E2E_USER_EMAIL >/dev/null 2>&1 || fail "missing E2E_USER_EMAIL in $e2e_env"
  env_file_value "$e2e_env" E2E_USER_PASSWORD >/dev/null 2>&1 || fail "missing E2E_USER_PASSWORD in $e2e_env"

  if [[ -n "$EXPECTED_URL" && "$EXPECTED_URL" =~ ^https?://(localhost|127\.0\.0\.1) ]]; then
    fail "production E2E must not target localhost: $EXPECTED_URL"
  fi

  say "Correct production E2E sequence:"
  say "  cd klai-portal/frontend"
  say "  source .env.local"
  say "  npm run test:e2e:prod"
}

case "$MODE" in
  local)
    mode_local
    ;;
  prod-e2e)
    mode_prod_e2e
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    exit 2
    ;;
esac

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
  say ""
  say "Warnings:"
  for item in "${WARNINGS[@]}"; do
    say "  - $item"
  done
fi

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  say ""
  say "Failures:"
  for item in "${FAILURES[@]}"; do
    say "  - $item"
  done
  exit 1
fi

say ""
say "Status: OK"
