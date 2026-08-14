# Pinned Caddy versions per SPEC-CI-TRIVY-POLICY-001 + infra/servers.md
# "never :latest in production". Renovate watches caddy:* tags for new
# patches.
#
# Note: the .trivyignore.yaml exemptions here exist because Caddy 2.11.3
# shipped a vulnerable go-jose and a builder compiled with Go 1.26.3.
# 2.11.4 may or may not close them — Trivy runs on the rebuilt image in CI
# and is the arbiter. If it goes green, drop the matching entries rather
# than letting them ride to their 2026-09-15 expiry.
#
# Bumping this file is NOT a pin flip: this is a custom xcaddy build with
# the hetzner-DNS and ratelimit plugins, so the change only reaches
# production once CI rebuilds and republishes ghcr.io/getklai/caddy-hetzner.
FROM caddy:2.11.4-builder-alpine AS builder

# The two --replace lines pin transitive dependencies, not plugins. Caddy
# 2.11.4's own graph selects versions that Trivy flags with a fix available:
#
#   golang.org/x/net        CVE-2026-46600       fixed in 0.56.0
#   google.golang.org/grpc  GHSA-hrxh-6v49-42gf  fixed in 1.82.1
#
# Both get fixed at source rather than earning a .trivyignore entry, which is
# reserved for findings that have no fix to apply.
#
# It has to be --replace, not --with: --with also writes a blank import into
# the generated main.go, and golang.org/x/net has no root package, so the build
# dies with "cannot find module providing package golang.org/x/net" (tried it —
# 3ccaaddb9). --replace writes only the go.mod directive.
#
# x/net goes to 0.58.0 rather than the 0.56.0 minimum because that is what the
# module graph resolves to once x/net is a direct requirement — this pins
# forward to where the tree already wants to be, not down at the CVE floor.
#
# Both plugins above are deliberately unversioned, so the resolved graph drifts
# between builds: a plain 2.11.4 build selected x/net 0.55.0 in CI and 0.57.0
# locally on the same day. These pins make the two dependencies that actually
# carry findings deterministic. Drop a line once Caddy ships a release that
# already selects at or above the fix.
RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2 \
    --with github.com/mholt/caddy-ratelimit \
    --replace golang.org/x/net=golang.org/x/net@v0.58.0 \
    --replace google.golang.org/grpc=google.golang.org/grpc@v1.82.1

FROM caddy:2.11.4-alpine

# Apply Alpine package security updates that landed AFTER the
# `caddy:2.11.4-alpine` image was published. These packages are exposed
# through Caddy's HTTPS edge, so real package fixes are preferred over
# ignore-entries. Keep this list targeted so the build stays reproducible;
# broader base-image movement remains Renovate-owned.
RUN apk update && apk upgrade --no-cache \
        nghttp2 nghttp2-libs libcrypto3 libssl3 \
        curl libcurl c-ares

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# SEC-018 F-029: Caddy deliberately runs as root. This is the upstream
# caddy:* image default and is required for binding to privileged ports
# 80 and 443 without cap_add. The alternative (setcap CAP_NET_BIND_SERVICE)
# adds complexity for marginal benefit — the Caddy binary is the single
# edge reverse-proxy, well-reviewed, and the container is isolated on
# klai-net with only 80/443/443-udp exposed. This is the standard
# deployment pattern for reverse proxies and is explicitly accepted risk.
