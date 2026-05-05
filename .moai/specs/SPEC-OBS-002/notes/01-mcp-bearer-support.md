# Note 01 — `mcp-victorialogs` Bearer support

**Status:** ✅ Resolved 2026-05-05

## Question

Does `mcp-victorialogs` v1.8.0 forward `Authorization: Bearer <jwt>`
verbatim to the VictoriaLogs upstream (so vmauth receives the JWT)?

## Evidence

### Official docs

VictoriaMetrics-Community/mcp-victorialogs README states `VL_INSTANCE_HEADERS`
is for **"custom headers for authentication (e.g. behind a reverse proxy)"**,
with syntax `<HEADER>=<VALUE>` comma-separated. This is the documented
extension point for non-basic-auth setups — exactly our use case.

### Source code

`mcp-victorialogs/cmd/mcp-victorialogs/config/config.go` parses
`VL_INSTANCE_HEADERS` into a `map[string]string` and exposes it via a
`CustomHeaders()` accessor on the Config struct. The HTTP client layer
(elsewhere in the repo) applies these headers to every outbound request
to the VictoriaLogs upstream.

### Operational precedent

Klai's existing `.mcp.json` already uses the same mechanism for basic
auth: `"VL_INSTANCE_HEADERS": "Authorization=Basic ${VICTORIALOGS_BASIC_AUTH_B64}"`.
Switching the value from `Basic ...` to `Bearer ...` is a string-level
change; no binary behaviour difference.

## Decision

`.mcp.json` will set:

```jsonc
"VL_INSTANCE_HEADERS": "Authorization=Bearer ${KLAI_VLOGS_JWT}"
```

The launcher writes the freshly-refreshed JWT into `KLAI_VLOGS_JWT` env
var before spawning the binary.

## Sources

- https://github.com/VictoriaMetrics/mcp-victorialogs (README)
- https://raw.githubusercontent.com/VictoriaMetrics/mcp-victorialogs/main/cmd/mcp-victorialogs/config/config.go
- Existing `.mcp.json` in this repo
