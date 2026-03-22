FROM caddy:builder AS builder

RUN xcaddy build \
    --with github.com/caddy-dns/hetzner/v2 \
    --with github.com/mholt/caddy-ratelimit

FROM caddy:latest

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
