# Pinned Caddy versions — closes two gaps simultaneously:
# 1. SPEC-CI-TRIVY-POLICY-001 PR #2: clears 6 HIGH/CRITICAL findings in
#    smallstep/certificates (CVE-2026-30836), grpc (CVE-2026-33186),
#    go-jose v3+v4 (CVE-2026-34986), otel (CVE-2026-39883), and
#    nghttp2-libs (CVE-2026-27135) by rebuilding against Caddy 2.11.2's
#    refreshed go.mod tree.
# 2. infra/servers.md "never :latest in production" rule — the previous
#    `caddy:latest` and `caddy:builder` references were unpinned and
#    non-reproducible. Renovate now watches both tags for new patches.
FROM caddy:2.11.2-builder-alpine AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2 \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:2.11.2-alpine

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# SEC-018 F-029: Caddy deliberately runs as root. This is the upstream
# caddy:* image default and is required for binding to privileged ports
# 80 and 443 without cap_add. The alternative (setcap CAP_NET_BIND_SERVICE)
# adds complexity for marginal benefit — the Caddy binary is the single
# edge reverse-proxy, well-reviewed, and the container is isolated on
# klai-net with only 80/443/443-udp exposed. This is the standard
# deployment pattern for reverse proxies and is explicitly accepted risk.
