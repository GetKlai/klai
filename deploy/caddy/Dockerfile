# Pinned Caddy versions per SPEC-CI-TRIVY-POLICY-001 + infra/servers.md
# "never :latest in production". Renovate watches caddy:* tags for new
# patches.
#
# Note: Caddy 2.11.2's go.mod still ships several vulnerable Go deps
# (smallstep/certificates, grpc, go-jose v3+v4, otel/sdk). Those have
# upstream fixes but no Caddy release with the bumps yet. They are
# documented exemptions in deploy/caddy/.trivyignore.yaml with 2-month
# expiry — Renovate-driven Caddy bumps will close them.
FROM caddy:2.11.2-builder-alpine AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2 \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:2.11.2-alpine

# Apply Alpine package security updates that landed AFTER the
# `caddy:2.11.2-alpine` image was published. Specifically pulls in
# nghttp2 1.69.0-r0 (in v3.23 main since 2026-04-28) which closes
# CVE-2026-27135 (H2-DoS in nghttp2). nghttp2 is exposed via Caddy's
# HTTPS edge (clients use HTTP/2 by default), so a real fix is
# preferred over an ignore-entry. Pinned to `nghttp2 nghttp2-libs`
# specifically so the build stays reproducible — bumping the Alpine
# base image globally is a separate concern handled by Renovate.
RUN apk update && apk upgrade --no-cache nghttp2 nghttp2-libs

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# SEC-018 F-029: Caddy deliberately runs as root. This is the upstream
# caddy:* image default and is required for binding to privileged ports
# 80 and 443 without cap_add. The alternative (setcap CAP_NET_BIND_SERVICE)
# adds complexity for marginal benefit — the Caddy binary is the single
# edge reverse-proxy, well-reviewed, and the container is isolated on
# klai-net with only 80/443/443-udp exposed. This is the standard
# deployment pattern for reverse proxies and is explicitly accepted risk.
