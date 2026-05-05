---
id: SPEC-SEC-EDGE-CSP-001
version: "0.2.0"
status: in-progress
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-CODEBASE-AUDIT-001 (parent, Cluster E)
---

# SPEC-SEC-EDGE-CSP-001: Site-wide CSP hardening + edge-rate-limit + permissive CORS fix

## Summary

Verhardt edge security beyond `frame-ancestors` only: per-host CSP met script-src/style-src/connect-src/object-src, CORS hardening op scribe/research-api permissief regex met credentials, edge rate-limit op `/api/*` catch-all, HSTS preload, Permissions-Policy uitbreiding.

## Motivation

Per `reports/audit-2026-05-04/frontend-edge-security.md` (3.5 + 3.6):
- **TP-FE-2/EDGE-1 HIGH**: site-wide CSP only `frame-ancestors` — geen XSS-defense-in-depth
- **TP-EDGE-2 HIGH**: scribe + research-api permissief `allow_origin_regex` met `allow_credentials=True` (latent risk)
- **TP-EDGE-3 MED**: geen edge rate-limit op `/api/*` catch-all
- **TP-EDGE-4 MED**: HSTS mist `preload` directive
- **TP-EDGE-5 MED**: Permissions-Policy is sparse

Plus al deels gedekt door PR #313 (klai-docs `rehype-sanitize` voor TP-FE-1).

## Scope

### In scope

1. **Per-host CSP** in `deploy/caddy/Caddyfile`:
   - Default: `script-src 'self' 'wasm-unsafe-eval' https://*.sentry.io https://my.getklai.com; object-src 'none'; base-uri 'self'; form-action 'self' https://auth.getklai.com`
   - Grafana/admin: `unsafe-inline` toegestaan
   - klai-docs: stricter (na PR #313 rehype-sanitize)
   - Tenant LibreChat: aparte file
2. **scribe-api + research-api**: vervang `allow_origin_regex` Starlette CORS door `KlaiCORSMiddleware`-stijl hardcoded compiled regex (rejecting multi-label edge cases)
3. **Edge rate-limit** op Caddy `handle /api/*`: `rate_limit { zone api_per_ip { key {remote_host} events 600 window 1m } }`
4. **HSTS preload**: `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload` (na verificatie alle subdomains HTTPS-only) + submit naar hstspreload.org
5. **Permissions-Policy** uitbreiding: `interest-cohort=(), browsing-topics=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=(), autoplay=(self), fullscreen=(self), picture-in-picture=()`

### Already done (PR #313)

- klai-docs `rehype-sanitize` na `rehype-raw` (TP-FE-1)

### Out of scope

- Frontend dep upgrades (next 16.2.3, picomatch) — separate PR met `npm audit fix`

## Acceptance criteria

1. Caddyfile lint via `caddy validate` clean
2. CSP-headers per route via `curl -I` test
3. `tests/caddy/test_caddyfile_csp_headers.py` regression-guard
4. scribe + research-api CORS test: rejected origins krijgen geen ACAC header
5. Rate-limit verify: 601e request in 1 min krijgt 429
6. HSTS preload verifier OK voordat submit
7. SecurityHeaders.com score ≥ A

## Risks

| Risk | Mitigatie |
|---|---|
| CSP breaks Sentry / Mantine / BlockNote inline | Per-host policies + browser-test pre-deploy |
| Rate-limit blokkeert legitime burst-callers (frontend bulk-ops) | 600/min per-IP is ruim; partner-API heeft eigen 120/min |
| HSTS preload is permanent | Verifier pre-submit; 1-jaar `max-age` first |
| Permissions-Policy breekt bestaande feature | Test in dev-tenant eerst |

## References

- `reports/audit-2026-05-04/frontend-edge-security.md`
- `deploy/caddy/Caddyfile`
- `klai-portal/backend/app/middleware/klai_cors.py` — KlaiCORSMiddleware canonical
- https://hstspreload.org
