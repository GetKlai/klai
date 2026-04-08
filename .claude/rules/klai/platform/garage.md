---
paths:
  - "klai-infra/**/garage*"
  - "**/garage*"
  - "deploy/docker-compose*.yml"
---
# Garage (S3-compatible Object Storage)

## Config field names differ from docs (HIGH)

Garage v2.2.0 `[s3_web]` section uses `bind_addr`, not `web_bind_addr` as some docs suggest. Wrong field name causes a crash loop with no useful error message.

**Why:** Garage docs lag behind releases. The TOML parser silently ignores unknown keys and falls back to defaults, which may bind to nothing.

**Prevention:** Check the Garage source or changelog for the exact version deployed. After any config change, verify with `docker logs --tail 20 garage` immediately.

## No env var substitution in garage.toml (HIGH)

Putting `${VAR}` in `garage.toml` is treated as a literal string — Garage does NOT expand environment variables in its config file.

**Why:** Garage's TOML parser reads values as-is. Unlike Docker Compose or shell scripts, there is no interpolation layer.

**Prevention:** Use the `GARAGE_RPC_SECRET` env var (supported since Garage v0.8.2) instead of trying to template `rpc_secret` into the TOML. For other secrets, mount a pre-rendered config or use Docker entrypoint scripts to generate the file.

## Website mode + Caddy proxy instead of presigned URLs

**When:** Serving stored objects (images, documents) to browsers through a reverse proxy.

Presigned URLs from the minio SDK contain the internal Docker hostname (e.g., `garage:3900`) which is unreachable from browsers. Switching to Garage website mode + Caddy reverse proxy eliminates URL expiry, simplifies caching, and avoids hostname resolution issues entirely.

```
# Caddyfile snippet
handle /kb-images/* {
    reverse_proxy garage:3902
}
```

Garage website mode serves bucket contents over HTTP on a separate port (default 3902). Caddy proxies this path, and the browser never sees the internal hostname.

**Rule:** Use Garage website mode + Caddy reverse proxy for browser-facing object access. Never expose presigned URLs that contain Docker-internal hostnames.

## MinIO project status (MED)

The MinIO OSS repository was archived in December 2025 with no further security patches. Do not use MinIO server for new deployments. The minio Python SDK is still usable as an S3 client library (it speaks standard S3 protocol), but for the storage server use Garage or SeaweedFS.

**Why:** Archived repo means no CVE patches. The SDK is protocol-level and does not depend on the server project.

**Prevention:** For new S3-compatible storage needs, evaluate Garage (lightweight, Rust, EU-developed) or SeaweedFS. Only use the minio SDK as a client library, not the server.

## Env var secrets pattern

**When:** Configuring Garage in Docker Compose with secrets (RPC secret, admin tokens).

Never put secrets in `garage.toml` (no env var substitution, and CI compose-sync would overwrite them). Use the `GARAGE_RPC_SECRET` environment variable and pass admin tokens via env vars in the compose file.

```yaml
garage:
  environment:
    GARAGE_RPC_SECRET: ${GARAGE_RPC_SECRET}
  volumes:
    - ./garage/garage.toml:/etc/garage.toml:ro
```

**Rule:** All Garage secrets via env vars in docker-compose.yml, never in garage.toml.
