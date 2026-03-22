#!/usr/bin/env bash
# Build Caddy with Hetzner DNS plugin
# Run once on the server
set -euo pipefail

# Install xcaddy if not present
if ! command -v xcaddy &>/dev/null; then
    go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
    export PATH=$PATH:$(go env GOPATH)/bin
fi

xcaddy build \
    --with github.com/caddy-dns/hetzner \
    --with github.com/mholt/caddy-ratelimit \
    --output /usr/local/bin/caddy

echo "Caddy built: $(caddy version)"
