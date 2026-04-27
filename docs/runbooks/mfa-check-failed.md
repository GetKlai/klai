# Runbook — `mfa_check_failed`

> Linked from Grafana alerts `mfa_check_failed_rate_high` and
> `mfa_check_failed_fail_open_burst`
> (`deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml`).
>
> SPEC: [SPEC-SEC-MFA-001](../../.moai/specs/SPEC-SEC-MFA-001/spec.md)

## What this signal means

`portal-api`'s login handler (`klai-portal/backend/app/api/auth.py::login`)
emits a structured `mfa_check_failed` event whenever it cannot complete the
MFA enforcement check for a login attempt. The event covers two distinct
outcomes:

- `outcome="503"` — login was rejected with HTTP 503 + `Retry-After: 5`.
  This happens under `mfa_policy="required"` whenever Zitadel is unreachable,
  the `has_any_mfa` lookup raises, the pre-auth `find_user_by_email` returns
  5xx, or the `PortalOrg` DB fetch raises while we know there is a portal
  user. The user sees a "please retry in a moment" error.
- `outcome="fail-open"` — login proceeded WITHOUT enforcement. This happens
  under `mfa_policy in {"optional", "recommended"}` (deliberate trade-off in
  REQ-3) AND when the `portal_user` lookup itself raised before we could
  determine policy (provisioning grace per REQ-3.2).

Both arms emit the same event so the rate is fully observable in
VictoriaLogs.

## Event schema

```jsonc
{
  "event": "mfa_check_failed",
  "service": "portal-api",
  "level": "warning" | "error",
  "request_id": "...",          // bound by LoggingContextMiddleware
  "reason": "has_any_mfa_5xx" | "find_user_by_email_5xx"
          | "db_lookup_failed" | "unexpected",
  "mfa_policy": "required" | "optional" | "unresolved",
  "zitadel_status": 500 | null, // null on RequestError / DB failure
  "email_hash": "<sha256 of lowercased email>",
  "outcome": "503" | "fail-open"
}
```

Email is **never** logged in plaintext — only the SHA-256 hex digest of the
lowercased email is recorded (privacy + correlation).

## Alert summary

| Alert | Threshold | Severity | Source |
|---|---|---|---|
| `mfa_check_failed_rate_high` | >5 events in any rolling 5m | warning | LogsQL `_time:5m service:portal-api event:mfa_check_failed \| stats count() as n` (n > 5) |
| `mfa_check_failed_fail_open_burst` | >10 fail-open events in any rolling 1m | critical | LogsQL `_time:1m service:portal-api event:mfa_check_failed outcome:fail-open \| stats count() as n` (n > 10) |

## Triage — high-rate alert

1. **Locate the failing leg.** Run in Grafana → Explore (datasource:
   VictoriaLogs):

   ```
   _time:5m service:portal-api event:mfa_check_failed
     | stats by (reason, mfa_policy, outcome) count()
   ```

   The dominant `reason` tells you whether to look at Zitadel or at the
   portal DB.

2. **If `reason` is `has_any_mfa_5xx` or `find_user_by_email_5xx`:** the
   problem is Zitadel-side. Check:

   ```bash
   curl -fsS https://auth.getklai.com/debug/healthz
   ```

   Plus VictoriaLogs:

   ```
   _time:15m service:zitadel level:error
   ```

   See [zitadel.md](../../.claude/rules/klai/platform/zitadel.md) for the
   Login V2 / PAT / restart-flap escalation paths. A transient Zitadel
   restart flap (5xx for ~30 seconds during rolling deploys) is documented
   and self-resolves; sustained 5xx is a real outage.

3. **If `reason` is `db_lookup_failed`:** the portal_user / portal_orgs
   lookup raised. Most common cause is a category-A RLS GUC leak — see
   [portal-backend.md § Pool-GUC pollution](../../.claude/rules/klai/projects/portal-backend.md).
   Quick check:

   ```
   _time:15m service:portal-api ("RLS:" OR "InsufficientPrivilege")
   ```

   Pool exhaustion shows as `connection.*timeout` in the same window.

4. **If `reason` is `unexpected`:** an exception type that is neither
   `httpx.HTTPStatusError` nor `httpx.RequestError` raised inside
   `has_any_mfa`. Treat as a code regression — check the recent merges to
   `klai-portal/backend/app/services/zitadel.py` and
   `klai-portal/backend/app/api/auth.py`.

5. **Confirm user impact.** Each event with `outcome="503"` corresponds to a
   real user who was denied login. Cross-check support tickets opened in the
   last 30 minutes; reach out proactively if the count is non-trivial.

## Triage — fail-open burst alert

This is a higher-severity signal. A small steady-state of fail-opens is
expected (every Zitadel hiccup → some optional-policy users see a swallowed
warning). A *burst* means either a wider outage or a security event.

1. **Always check the rate alert first.** If
   `mfa_check_failed_rate_high` is also firing, both alerts share a root
   cause — triage the wider one (the high-rate alert) and the burst will
   subside as the underlying issue is resolved.

2. **Group the bursts to identify the cause:**

   ```
   _time:5m service:portal-api event:mfa_check_failed outcome:fail-open
     | stats by (mfa_policy, reason) count()
   ```

   Patterns:
   - `reason=has_any_mfa_5xx` and `mfa_policy=optional` dominating →
     Zitadel-wide outage affecting optional-policy orgs. This is the
     documented expected fail mode — no security action required, just wait
     for Zitadel recovery and let the rate alert run its `keepFiringFor`.
   - `reason=db_lookup_failed` dominating → DB-level issue affecting our
     ability to determine policy. See § "high-rate alert" step 3.
   - Single `reason=unexpected` repeating → likely a code regression. Check
     recent merges; consider rollback if the burst correlates with a deploy.

3. **Security escalation criteria.** Escalate to security on-call IF:
   - Zitadel is healthy (your `auth.getklai.com/debug/healthz` returns 200)
     AND DB is healthy AND the burst persists for >5 minutes.
   - The burst is concentrated on a single `email_hash` (suggests
     enumeration / credential stuffing during a noise-generating window).
   - You see a coincident spike in Caddy 4xx-on-/api/auth/login from a
     small number of source IPs.

   Mitigation while escalating: rate-limit `/api/auth/login` at Caddy
   (`@public_limit { not method GET }` is the existing pattern in
   `deploy/caddy/Caddyfile`).

4. **What NOT to do.**

   - Do **NOT** flip `mfa_policy="required"` to `"optional"` for an org "to
     stop the alert". The alert is the SPEC working as designed.
   - Do **NOT** silence the rule in Grafana. If the alert is genuinely
     wrong (false-positive on weekends, etc.) raise the threshold by editing
     `portal-mfa-rules.yaml` and link this runbook in the commit.

## Useful queries

```
# All fail-closed (503) events in the last hour, grouped by reason
_time:1h service:portal-api event:mfa_check_failed outcome:503
  | stats by (reason) count()

# Distinct affected user-hashes (for support cross-reference)
_time:1h service:portal-api event:mfa_check_failed
  | stats by (email_hash) count()

# Trace a single user's failed-login chain via request_id
request_id:<uuid>
```

## Related

- [SPEC-SEC-MFA-001](../../.moai/specs/SPEC-SEC-MFA-001/spec.md) — fail-closed
  semantics and event schema.
- [`klai-portal/backend/app/api/auth.py`](../../klai-portal/backend/app/api/auth.py)
  — `_emit_mfa_check_failed`, `_resolve_and_enforce_mfa`, refactored
  `login` handler.
- [`deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml`](../../deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml)
  — alert definitions.
- [`.claude/rules/klai/platform/zitadel.md`](../../.claude/rules/klai/platform/zitadel.md)
  — Zitadel availability, PAT rotation, Login V2 recovery.
- [`.claude/rules/klai/projects/portal-backend.md`](../../.claude/rules/klai/projects/portal-backend.md)
  — Pool-GUC pollution and RLS guard patterns.
- [`.claude/rules/klai/infra/observability.md`](../../.claude/rules/klai/infra/observability.md)
  — VictoriaLogs / Grafana MCP / log fields.
