---
paths:
  - "klai-infra/core-01/caddy/**"
  - "**/Caddyfile"
---
# Caddy

## Permissions-Policy (CRIT)
- `microphone=()` blocks `getUserMedia` entirely — no dialog, silent fail.
- Correct: `microphone=self` to allow same-origin mic access.
- After fix: users with cached "denied" must manually reset in browser settings.

## Tenant routing
- One wildcard `*.getklai.com` block for known services.
- Per-tenant LibreChat blocks appended at provisioning time by portal-api.
- Caddy does NOT auto-discover containers. New blocks must be appended + Caddy restarted.

## Reload (admin off)
- `admin off` disables the Admin API entirely. `POST /load` or `/reload` will fail.
- Reload by restarting the container via Docker SDK: `container.restart(timeout=10)`.
- ~1s TLS interruption. Acceptable at current scale (<50 tenants).

## Wildcard TLS
- Requires custom Caddy build with `github.com/caddy-dns/hetzner` plugin.
- Env var: `HETZNER_AUTH_API_TOKEN` (Hetzner DNS, not Cloud86).
- TLS block: `tls { dns hetzner {$HETZNER_AUTH_API_TOKEN} propagation_delay 120s }`.

## basic_auth + monitoring
- `basic_auth` blocks Uptime Kuma health checks.
- Add a named matcher for the health path BEFORE the `basic_auth` handler.

## log directive
- `log` is site-level, not handler-level. Cannot go inside `handle` blocks.
- Place at server block level (`*.example.com { log { ... } }`).

## header vs request_header (HIGH)
- `header` sets RESPONSE headers — backends never see them.
- `request_header` sets REQUEST headers forwarded to upstream.
- **When:** injecting `X-Request-ID` or other trace headers for backend services.
- **Source:** SPEC-INFRA-004 — used `header` initially, backends didn't receive the ID.

## Docker socket proxy
- portal-api accesses Docker via `tcp://docker-socket-proxy:2375` (never direct socket mount).
- Proxy allows: CONTAINERS, NETWORKS, POST, DELETE. Everything else denied.
