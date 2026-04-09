#!/usr/bin/env bash
# SSH tunnel to VictoriaLogs on core-01 for mcp-victorialogs.
# Resolves the container IP dynamically (IPs change on restart).
#
# Features:
#   - Checks if tunnel is already running (prevents duplicate tunnels)
#   - Checks if local port is already in use
#   - Health-checks VictoriaLogs after connecting
#   - Auto-reconnects on connection drop (up to 5 retries)
#
# Usage: ./scripts/victorialogs-tunnel.sh
#        ./scripts/victorialogs-tunnel.sh --check   (exit 0 if tunnel is up)
#        ./scripts/victorialogs-tunnel.sh --stop    (kill existing tunnel)
set -euo pipefail

CONTAINER="klai-core-victorialogs-1"
LOCAL_PORT=9428
REMOTE_PORT=9428
MAX_RETRIES=5
PIDFILE="${TMPDIR:-/tmp}/victorialogs-tunnel.pid"

check_tunnel() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    # PID exists, verify the port actually responds
    if curl -sf -o /dev/null -m 2 "http://localhost:$LOCAL_PORT/health" 2>/dev/null; then
      return 0
    fi
    # PID alive but port not responding — stale tunnel
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
  fi
  return 1
}

stop_tunnel() {
  if [ -f "$PIDFILE" ]; then
    local pid
    pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      echo "Tunnel stopped (PID $pid)."
    fi
    rm -f "$PIDFILE"
  else
    echo "No tunnel running."
  fi
}

resolve_ip() {
  ssh core-01 "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $CONTAINER" 2>/dev/null
}

health_check() {
  # VictoriaLogs requires basic auth — use env var if set
  local auth_header=""
  if [ -n "${VICTORIALOGS_BASIC_AUTH_B64:-}" ]; then
    auth_header="-H Authorization:Basic ${VICTORIALOGS_BASIC_AUTH_B64}"
  fi
  curl -sf -o /dev/null -m 3 $auth_header "http://localhost:$LOCAL_PORT/health" 2>/dev/null
}

# Handle subcommands
case "${1:-}" in
  --check)
    if check_tunnel; then
      echo "Tunnel is up (PID $(cat "$PIDFILE"), port $LOCAL_PORT)."
      exit 0
    else
      echo "Tunnel is not running."
      exit 1
    fi
    ;;
  --stop)
    stop_tunnel
    exit 0
    ;;
esac

# Prevent duplicate tunnels
if check_tunnel; then
  echo "Tunnel already running (PID $(cat "$PIDFILE"), port $LOCAL_PORT)."
  exit 0
fi

# Check if port is in use by something else
if lsof -i ":$LOCAL_PORT" -sTCP:LISTEN &>/dev/null; then
  echo "ERROR: Port $LOCAL_PORT is already in use by another process." >&2
  lsof -i ":$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null | head -3 >&2
  exit 1
fi

# Resolve container IP
IP=$(resolve_ip)
if [ -z "$IP" ]; then
  echo "ERROR: Could not resolve IP for $CONTAINER on core-01." >&2
  echo "Is VictoriaLogs running? Check: ssh core-01 'docker ps | grep victorialogs'" >&2
  exit 1
fi

echo "VictoriaLogs container IP: $IP"

# Connect with auto-reconnect
retry=0
while [ $retry -lt $MAX_RETRIES ]; do
  echo "Tunneling localhost:$LOCAL_PORT → core-01 → $CONTAINER ($IP:$REMOTE_PORT)"

  # Start tunnel in background
  ssh -N -L "$LOCAL_PORT:$IP:$REMOTE_PORT" \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes \
      core-01 &
  SSH_PID=$!
  echo "$SSH_PID" > "$PIDFILE"

  # Wait for tunnel to establish
  sleep 2

  if ! kill -0 "$SSH_PID" 2>/dev/null; then
    echo "ERROR: SSH tunnel failed to start." >&2
    rm -f "$PIDFILE"
    retry=$((retry + 1))
    if [ $retry -lt $MAX_RETRIES ]; then
      echo "Retrying ($retry/$MAX_RETRIES)..."
      # Re-resolve IP in case container restarted
      IP=$(resolve_ip)
      [ -z "$IP" ] && { echo "ERROR: Container IP resolution failed." >&2; exit 1; }
      sleep 2
    fi
    continue
  fi

  # Health check
  if health_check; then
    echo "Tunnel established and healthy (PID $SSH_PID)."
    echo "Press Ctrl+C to stop."
    # Wait for SSH to exit (user Ctrl+C or connection drop)
    wait "$SSH_PID" 2>/dev/null || true
    rm -f "$PIDFILE"

    # If we get here, the tunnel dropped
    retry=$((retry + 1))
    if [ $retry -lt $MAX_RETRIES ]; then
      echo "Tunnel dropped. Reconnecting ($retry/$MAX_RETRIES)..."
      IP=$(resolve_ip)
      [ -z "$IP" ] && { echo "ERROR: Container IP resolution failed." >&2; exit 1; }
      sleep 2
    fi
  else
    echo "WARNING: Tunnel started but health check failed. VictoriaLogs may require auth." >&2
    echo "Tunnel established (PID $SSH_PID). Press Ctrl+C to stop."
    wait "$SSH_PID" 2>/dev/null || true
    rm -f "$PIDFILE"
    break
  fi
done

if [ $retry -ge $MAX_RETRIES ]; then
  echo "ERROR: Max retries ($MAX_RETRIES) reached. Giving up." >&2
  rm -f "$PIDFILE"
  exit 1
fi
