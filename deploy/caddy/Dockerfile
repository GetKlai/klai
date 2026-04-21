FROM caddy:builder AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2 \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:latest

COPY --from=builder /usr/bin/caddy /usr/bin/caddy

# SEC-018 F-029: Caddy deliberately runs as root. This is the upstream
# caddy:latest default and is required for binding to privileged ports
# 80 and 443 without cap_add. The alternative (setcap CAP_NET_BIND_SERVICE)
# adds complexity for marginal benefit — the Caddy binary is the single
# edge reverse-proxy, well-reviewed, and the container is isolated on
# klai-net with only 80/443/443-udp exposed. This is the standard
# deployment pattern for reverse proxies and is explicitly accepted risk.
