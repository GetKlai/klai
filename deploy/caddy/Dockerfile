# Pinned Caddy versions per SPEC-CI-TRIVY-POLICY-001 + infra/servers.md
# "never :latest in production". Renovate watches caddy:* tags for new
# patches.
#
# Note: Caddy 2.11.3 still ships a vulnerable go-jose dependency, and
# its builder image was compiled with Go 1.26.3. Those upstream gaps are
# documented exemptions in deploy/caddy/.trivyignore.yaml with 2-month
# expiry — Renovate-driven Caddy bumps will close them.
FROM caddy:2.11.3-builder-alpine AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2 \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:2.11.3-alpine

# Apply Alpine package security updates that landed AFTER the
# `caddy:2.11.3-alpine` image was published. These packages are exposed
# through Caddy's HTTPS edge, so real package fixes are preferred over
# ignore-entries. Keep this list targeted so the build stays reproducible;
# broader base-image movement remains Renovate-owned.
RUN apk update && apk upgrade --no-cache nghttp2 nghttp2-libs libcrypto3 libssl3

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# SEC-018 F-029: Caddy deliberately runs as root. This is the upstream
# caddy:* image default and is required for binding to privileged ports
# 80 and 443 without cap_add. The alternative (setcap CAP_NET_BIND_SERVICE)
# adds complexity for marginal benefit — the Caddy binary is the single
# edge reverse-proxy, well-reviewed, and the container is isolated on
# klai-net with only 80/443/443-udp exposed. This is the standard
# deployment pattern for reverse proxies and is explicitly accepted risk.
