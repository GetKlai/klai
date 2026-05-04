---
id: SPEC-SEC-CALLBACK-DERIVE-001
version: "0.1.0"
status: draft
created: "2026-04-30"
updated: "2026-04-30"
author: MoAI
priority: medium
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-04-30 | MoAI | Stub created from the polish-round self-review of SPEC-SEC-HYGIENE-001 REQ-20.5. Hand-curated allowlist drift-class is now structurally addressed by deriving the trusted set from Zitadel's registered redirect_uris at startup, eliminating the audit lag entirely. |

# SPEC-SEC-CALLBACK-DERIVE-001: Derive callback URL allowlist from Zitadel OIDC config at startup

## Overview

Replace the hand-curated `_STATIC_SYSTEM_SUBDOMAINS` frozenset and the hardcoded `_TENANT_HOST_PREFIXES` in `klai-portal/backend/app/api/auth.py` with a runtime-derived set fetched from Zitadel's Management API at process start. The validator's "trusted hosts" set becomes a subset of "every redirect_uri actually registered on a Klai-Platform OIDC client", refreshed periodically and on cache invalidation.

This is the structural answer to the failure class that produced two prod outages in 24 hours (PR #230 and PR #243): the ground truth for "is this hostname a valid OIDC callback target?" lives in Zitadel; any hand-maintained mirror of that ground truth is a guaranteed source of drift. The nightly drift check (`scripts/check_zitadel_oidc_drift.py`) catches the divergence within 24 hours but does not eliminate the failure class — a new OIDC app added in Zitadel still breaks logins for up to a day.

## Environment

- **Service**: klai-portal-api (FastAPI, Python 3.13)
- **Module**: `klai-portal/backend/app/api/auth.py` — `_validate_callback_url` and helpers
- **Existing artefacts** (replaced by this SPEC):
  - `_STATIC_SYSTEM_SUBDOMAINS` frozenset (hand-curated)
  - `_TENANT_HOST_PREFIXES` frozenset (hand-curated)
  - The contract test `test_static_system_subdomains_set_includes_known_oidc_apps` (becomes redundant, can be removed)
- **External dependency**: Zitadel Management API endpoint `/management/v1/projects/{project_id}/apps/_search` reachable via existing `ZitadelClient` instance.
- **Reuse**: the parsing logic in `scripts/check_zitadel_oidc_drift.py` is the reference implementation — moves into `app/services/zitadel_callback_hosts.py`.

## Assumptions

- A1: Zitadel is reachable from portal-api at startup. If not, portal-api must NOT crash — degrade to a fail-safe minimal set (apex + FRONTEND_URL host) and log loudly so the operator sees the degraded state.
- A2: The set of registered redirect_uris is small (currently 8 OIDC apps × ~2 URIs each ≈ 16 entries). Single management-API call returns the full list.
- A3: Zitadel's `redirect_uris` field is the source of truth — anything registered there is a legitimate callback target by definition (Zitadel itself enforces it as primary defense).
- A4: A 5-minute refresh cadence is sufficient — adding an OIDC app is a manual operator action with minutes-of-rollout, not seconds.

## Requirements

### R1 — Ubiquitous: Zitadel is the source of truth

The system SHALL derive the callback-URL trusted-host set at process start from Zitadel's registered OIDC redirect_uris. Hand-curated frozensets in code SHALL be removed.

### R2 — Event-driven: refresh on TTL + on-demand

WHEN the callback-host cache is older than its TTL (default 300s) AND a callback-validation request arrives, THE service SHALL refresh the cache from Zitadel before serving the request. WHEN an admin endpoint or signal triggers an explicit refresh (e.g. after a Zitadel OIDC app addition), the cache SHALL be invalidated immediately.

### R3 — State-driven: degraded mode on Zitadel outage

IF Zitadel is unreachable at startup OR a refresh fails, THE service SHALL fall back to a minimal hardcoded safe set (`{settings.domain, urlparse(settings.frontend_url).hostname, "localhost", "127.0.0.1"}`) AND emit a structlog ERROR `callback_host_degraded` every refresh attempt until Zitadel returns. The validator SHALL still operate — strict-fail-closed is worse than a degraded but functional state because the fallback set still protects against the open-redirect class while still letting login work for portal-only flows.

### R4 — Unwanted Behavior: prevent test bypass via mock

The validator SHALL NOT accept a Zitadel-derived set that has been mocked to return all hosts (e.g. `{"*": True}`). The test infrastructure MUST inject a finite explicit set; any set with more than 100 entries OR containing wildcard tokens SHALL be rejected at construction time as a likely test-fixture leak.

### R5 — Optional: per-tenant pattern detection

Where the derived set contains an obvious per-tenant pattern (e.g. multiple `chat-{slug}` entries), the validator MAY synthesize a `chat-{slug-allowlist}` rule rather than enumerating every per-tenant host explicitly. This is an optimisation: it avoids a Zitadel write step every time a new tenant signs up. The trade-off is that an attacker registering a `chat-attacker` Zitadel app would gain `chat-attacker.{domain}` access until removed; mitigated by Zitadel's existing `redirect_uri` exact-match check.

## Specifications

### Refresh strategy

```
┌────────────────────────────┐
│ Startup                    │
│  1. Try Zitadel fetch      │
│  2. If success → cache it  │
│  3. If fail → use fallback │
│  4. Log either way         │
└────────────────────────────┘
              │
              ▼
┌────────────────────────────┐         every 300s OR on-demand
│ Hot path                   │ ◄────────────────────────────────┐
│  callback validates        │                                   │
│  against cached set        │                                   │
└────────────────────────────┘                                   │
              │                                                  │
              ▼                                                  │
┌────────────────────────────┐                                   │
│ Cache TTL expired?         │ ─── yes ───► trigger refresh ─────┘
│   no  → use cache          │
└────────────────────────────┘
```

### Fallback minimal set

When Zitadel is unreachable:

```python
_FALLBACK_HOSTS = {
    settings.domain,                                       # bare apex
    urlparse(settings.frontend_url).hostname,              # FRONTEND_URL host
    "localhost",
    "127.0.0.1",
}
```

This is intentionally narrower than the runtime-derived set. Operations during a Zitadel outage can still complete the portal login flow. LibreChat / Grafana / per-tenant chat would temporarily fail; that's an acceptable trade-off.

### Audit

- Replace `test_static_system_subdomains_set_includes_known_oidc_apps` with a runtime-integration test that asserts `_get_trusted_hosts()` calls Zitadel exactly once on first request.
- Replace `scripts/check_zitadel_oidc_drift.py` with a much-simpler "Zitadel is reachable" smoke (the workflow keeps the same name and cron, the script becomes a 5-line health probe).
- Update `_STATIC_SYSTEM_SUBDOMAINS` deletion in `auth.py` is FROZEN — once removed, never re-add as a bypass for testing convenience.

### Test strategy

- Mock `ZitadelClient.list_oidc_apps()` in tests to return known fixtures.
- Parametrize across 3 fixture sets: typical (current 8 apps), empty, and degraded (Zitadel raises).
- Existing `test_validate_callback_url.py` tests get a new fixture that injects a synthetic Zitadel response containing the host classes the existing tests assert on.

## Files Affected

- `klai-portal/backend/app/api/auth.py` — remove `_STATIC_SYSTEM_SUBDOMAINS`, `_TENANT_HOST_PREFIXES`; rewire `_system_callback_hosts()` to call into the new service.
- `klai-portal/backend/app/services/zitadel_callback_hosts.py` (new) — fetch + parse + cache logic. Wraps `ZitadelClient` with the cache + fallback semantics.
- `klai-portal/backend/app/services/zitadel.py` — extend with a `list_oidc_apps()` method (currently the script does this raw via httpx; this SPEC formalises it).
- `klai-portal/backend/tests/test_validate_callback_url.py` — switch fixtures to inject a synthetic Zitadel response.
- `klai-portal/backend/tests/test_zitadel_callback_hosts.py` (new) — cache + TTL + refresh + fallback tests.
- `scripts/check_zitadel_oidc_drift.py` — simplify to "Zitadel is reachable + has at least one OIDC app" smoke. Move detailed parsing logic into the new service module.
- `.moai/specs/SPEC-SEC-HYGIENE-001/spec.md` — add HISTORY entry pointing at this SPEC; mark REQ-20.5's hand-curated approach as superseded.

## MX Tag Plan

- `_STATIC_SYSTEM_SUBDOMAINS` → removed entirely (no MX trace needed; document removal in HISTORY).
- `_TENANT_HOST_PREFIXES` → same.
- `_system_callback_hosts()` → MX:ANCHOR (high fan_in retained; just changes implementation).
- New `ZitadelCallbackHostCache` (or equivalent) class → MX:ANCHOR + MX:WARN (cache-with-fallback is a security control; failure modes need explicit handling).

## Exclusions

- **Replacing the slug-allowlist DB read.** REQ-20.1 (active tenant slugs from `portal_orgs.slug WHERE deleted_at IS NULL`) stays. This SPEC only removes the STATIC system-subdomain hand-curation, not the dynamic tenant-slug part.
- **Auto-creating Zitadel OIDC apps from portal code.** That's the inverse direction (klai writing to Zitadel) — out of scope here.
- **Federation / multi-Zitadel.** Single Zitadel instance assumption holds.

## Implementation Notes (for `/moai run`)

- Read `scripts/check_zitadel_oidc_drift.py` first — its parsing logic is the seed for `app/services/zitadel_callback_hosts.py`. Don't re-derive; lift and adapt.
- The existing `ZitadelClient` already handles auth (PAT) and base URL — extend it with one new method, don't open a parallel client.
- Cache invalidation hook: add an `invalidate_callback_host_cache()` function mirroring `invalidate_tenant_slug_cache()`. Called from any future admin endpoint that adds/removes Zitadel apps.
- Fallback set selection: at the design point where you pick between "fail closed" and "degrade to minimal set", choose minimal set. The argument is in R3 — fail-closed during a Zitadel outage means portal login also breaks, which forces operators to firefight two systems at once instead of one.
- Test fixture: instead of mocking `ZitadelClient.list_oidc_apps()` directly, build a `_TestZitadelCallbackHostCache` subclass that overrides the fetch method. Cleaner separation of concerns.
- Performance: at 300s TTL, the cache-miss latency is amortised across thousands of requests. No need for background refresh threads (premature optimisation).
