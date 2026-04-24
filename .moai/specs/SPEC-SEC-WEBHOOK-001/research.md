# Research -- SPEC-SEC-WEBHOOK-001

Codebase analysis supporting the webhook authentication hardening SPEC.

## Finding context (from SECURITY.md, 2026-04-22)

Cornelis's audit surfaced three related webhook-auth defects in `klai-portal/backend`:

1. **Finding #2 (CRIT)** -- `POST /api/bots/internal/webhook` (Vexa) trusts any source IP starting with `172.`, `10.`, or `192.168.`. Because uvicorn is launched without `--proxy-headers`, every request arriving from Caddy has `request.client.host` set to the Caddy container's Docker IP, which always matches that prefix. The Bearer token gate at `meetings.py:55-58` is therefore unreachable for any real external caller. The endpoint is effectively open.
2. **Finding #3 (HIGH)** -- `POST /api/webhooks/moneybird` fails open when `MONEYBIRD_WEBHOOK_TOKEN` is empty: the handler at `webhooks.py:24` guards the token check with `if settings.moneybird_webhook_token:`, so when the env var is missing the request is accepted without any token validation and a 200 is returned.
3. **Finding #4 (HIGH)** -- The same Moneybird handler at `webhooks.py:26` uses `!=` to compare tokens, a non-constant-time comparison that leaks information through wall-clock timing. It then returns 200 on mismatch (rather than 401), which makes the defect invisible to the sender and any naive monitoring.

This SPEC addresses all three in one locality. Grouping them is a fix-locality decision (all three live in `klai-portal/backend/app/api/`), not a severity decision -- see the tracker SPEC-SEC-AUDIT-2026-04.

---

## Current uvicorn launch flags

From `klai-portal/backend/entrypoint.sh:16`:

```sh
exec uvicorn app.main:app --host 0.0.0.0 --port 8010 "$@"
```

No `--proxy-headers`. No `--forwarded-allow-ips`. The `"$@"` positional forwards any
container-level CMD override, but `deploy/docker-compose.yml` does NOT override them for
portal-api, so the effective launch is the literal above.

Consequence: FastAPI/Starlette sees `request.client.host` as the TCP peer of uvicorn --
which on `klai-net` is always the Caddy container's Docker IP (the only upstream in the
deployed topology). Every legitimate external webhook therefore appears to come from a
`172.x`/`10.x` address.

Once `--proxy-headers --forwarded-allow-ips=<caddy-ip>` is set, uvicorn will read the
`X-Forwarded-For` header from Caddy (see next section) and populate `request.client.host`
with the real external caller IP.

---

## Caddy X-Forwarded-For and X-Request-ID configuration

From `deploy/caddy/Caddyfile:30-35`:

```caddy
# Generate X-Request-ID for trace correlation across Caddy -> backend services.
# request_header sets it on the upstream REQUEST (not response), so backends see it.
@no-request-id {
    not header X-Request-ID *
}
request_header @no-request-id X-Request-ID "{http.request.uuid}"
```

Caddy's `reverse_proxy` directive (line 208: `handle /api/* { reverse_proxy portal-api:8010 }`)
sets `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host` automatically -- this is
Caddy default behaviour, not something we configured. Documented in the Caddy docs
(reverse_proxy default header_up directives).

The pitfalls rule `caddy.md` explicitly calls out the `header` vs `request_header`
distinction: `header` sets RESPONSE headers (invisible to backends), `request_header` sets
REQUEST headers (visible to backends). Caddy's reverse_proxy internally uses the
request-header path for `X-Forwarded-For`, so uvicorn CAN trust it once `--proxy-headers` is
set.

Observed behaviour verified by the portal-api middleware at
`klai-portal/backend/app/trace.py` (uses `X-Request-ID` from incoming headers with a UUID
fallback) -- structlog entries from portal-api already carry `request_id` populated by Caddy,
proving the header reaches the backend intact.

**What's missing:** uvicorn's opt-in to trusting `X-Forwarded-For`. Without `--proxy-headers`,
the header is received but ignored by the ASGI layer. With `--proxy-headers` and a restrictive
`--forwarded-allow-ips`, uvicorn replaces `request.client.host` with the leftmost entry of
`X-Forwarded-For` (i.e. the real external client) as long as the immediate TCP peer is in the
allowlist.

---

## Inventory of webhook endpoints in klai-portal

Result of `grep -rn "@router\.post.*webhook\|_require_webhook_secret"` across
`klai-portal/backend/app/api/**/*.py`:

| Endpoint | File:line | Auth mechanism | Notes |
|---|---|---|---|
| `POST /api/bots/internal/webhook` | `meetings.py:644` | `_require_webhook_secret` (Bearer + IP-bypass) | SPEC-VEXA-003 Vexa post-meeting hook. Called by Vexa `api-gateway` per `POST_MEETING_HOOKS` env in `deploy/docker-compose.yml:886`. Currently `http://portal-api:8010/api/bots/internal/webhook` with no Authorization header -- relies on the IP-bypass defect to pass. |
| `POST /api/webhooks/moneybird` | `webhooks.py:17` | Payload field `webhook_token` compared with `!=` inside a `if settings.moneybird_webhook_token:` guard | Moneybird's native webhook format embeds the token in the JSON body, not the `Authorization` header. This is Moneybird's contract, not something we chose. |

### No Zitadel Actions webhook exists in klai-portal/backend

The stub mentioned "Zitadel Actions" as a third inventory item. A repo-wide grep for
`zitadel.*action` and a listing of webhook-adjacent endpoints in `klai-portal/backend/app/api/`
(17 files matched the broader keyword search, all inspected) returns no `POST
.../webhook` route specific to Zitadel. Zitadel Actions reach portal-api via `/internal/*`
endpoints (covered by SPEC-SEC-005 and SPEC-SEC-INTERNAL-001), not via a dedicated webhook
route. This SPEC therefore scopes itself to the two real webhook endpoints above.

---

## Config-layer state (config.py)

From `klai-portal/backend/app/core/config.py`:

- `moneybird_webhook_token: str = ""` (line 41) -- optional by default; no validator.
- `vexa_webhook_secret: str = ""` (line 167) -- optional by default, but...
- `_require_vexa_webhook_secret` model_validator at lines 235-248 raises `ValueError` if
  the value is empty or whitespace-only, preventing startup. This is the exact pattern we
  need to replicate for Moneybird (REQ-3.1).

The pattern is well-established and well-tested. The SPEC's fail-closed requirement reuses
it verbatim.

---

## Trusted-proxy allowlist approach

`--forwarded-allow-ips` accepts a comma-separated list of IPs or CIDRs. Options considered:

### Option A: Caddy container IP (chosen)

Example: `--forwarded-allow-ips=172.18.0.5` (whatever Caddy is assigned on `klai-net`).

- Narrow blast radius: only Caddy is trusted to set `X-Forwarded-For`.
- Drawback: Caddy's Docker IP can change on restart. Must be re-verified at every deploy.
  Mitigation: document in `entrypoint.sh` comment + `deploy.md` runbook.
- Alternative mitigation: deploy Caddy with a static IP on `klai-net` via `ipv4_address` in
  `docker-compose.yml`. This is a follow-up infra change, not blocking for this SPEC.

### Option B: `klai-net` subnet (rejected)

Example: `--forwarded-allow-ips=172.18.0.0/16`.

- Broad: every sibling container on `klai-net` can forge `X-Forwarded-For` values.
- Effectively re-introduces the IP-bypass defect one layer up the stack.
- REJECTED.

### Option C: `*` wildcard (rejected)

- Accepts `X-Forwarded-For` from any source including the raw internet.
- Equivalent to unauthenticated forwarding.
- REJECTED.

### Option D: Caddy container hostname (rejected by uvicorn)

`--forwarded-allow-ips` does not resolve hostnames. Must be a literal IP/CIDR. Can be
interpolated at entrypoint time via `getent hosts caddy | awk '{print $1}'` if needed, but
that adds container-startup dependencies. We prefer the explicit IP from SOPS or a
compose-injected env var.

Proposed mechanism: add `CADDY_CONTAINER_IP` as an env var in portal-api's compose
service block, interpolated at deploy time, and reference it in `entrypoint.sh`:

```sh
exec uvicorn app.main:app \
    --host 0.0.0.0 --port 8010 \
    --proxy-headers \
    --forwarded-allow-ips="${CADDY_CONTAINER_IP}" \
    "$@"
```

The implementation SPEC-RUN phase will verify Caddy's actual IP and wire this env var.

---

## Why this grouping is safe to ship together

The three fixes share a single code locality (webhook auth) and have no cross-dependencies
with other SPECs:

- No schema migration (unlike SPEC-SEC-005's audit table reuse).
- No RLS policy changes (unlike SPEC-SEC-TENANT-001).
- No cross-service protocol change (Moneybird still POSTs its native body; Vexa's
  `POST_MEETING_HOOKS` gets a Bearer header added as a config tweak).

The backward-compatibility risk is explicit: when `--proxy-headers` is deployed but Vexa's
`POST_MEETING_HOOKS` has not yet been updated to include a Bearer, the Vexa webhook will
start returning 401. This is the intentional forcing function -- silent fail-open was the
bug. The runbook must schedule the Vexa env-var update in the same deploy window.

---

## Open questions (tracked, not blocking)

- Should we also move Moneybird's `webhook_token` from the JSON body to an `Authorization`
  header? Moneybird does not support that -- the body format is fixed by their platform. Keep
  the body-based token and just fix the comparison and failure semantics.
- Should the Vexa Bearer secret be separate from the one used by any future Vexa->portal-api
  calls? Today there is only one direction (`POST_MEETING_HOOKS`), so a single
  `VEXA_WEBHOOK_SECRET` suffices. If Vexa adds other callbacks later, split then.
- Does the webhook need an audit trail analogous to SPEC-SEC-005 REQ-2? Left to a follow-up
  SPEC if post-incident forensics proves it necessary. Current VictoriaLogs structlog capture
  (30 days) is sufficient for the webhook rate.

---

## References

- [klai-portal/backend/app/api/meetings.py#L46-L58](../../../klai-portal/backend/app/api/meetings.py#L46) -- `_require_webhook_secret` (current, buggy)
- [klai-portal/backend/app/api/webhooks.py#L17-L81](../../../klai-portal/backend/app/api/webhooks.py#L17) -- Moneybird handler (current, buggy)
- [klai-portal/backend/app/core/config.py#L235-L248](../../../klai-portal/backend/app/core/config.py#L235) -- `_require_vexa_webhook_secret` (pattern to replicate)
- [klai-portal/backend/entrypoint.sh#L16](../../../klai-portal/backend/entrypoint.sh#L16) -- uvicorn launch (to be modified)
- [deploy/caddy/Caddyfile#L30-L35](../../../deploy/caddy/Caddyfile#L30) -- X-Request-ID injection (confirms request_header pattern works)
- [deploy/docker-compose.yml#L886](../../../deploy/docker-compose.yml#L886) -- `POST_MEETING_HOOKS` config
- [klai-portal/backend/tests/test_meetings_webhook_auth.py](../../../klai-portal/backend/tests/test_meetings_webhook_auth.py) -- existing tests, including the IP-bypass test that will be deleted/inverted (line 117)
- Pitfalls: `.claude/rules/klai/platform/caddy.md` (header vs request_header), `.claude/rules/klai/infra/observability.md` (request_id propagation)

---

## Internal-wave additions (2026-04-24)

During the internal-wave audit, the uvicorn trusted-proxy defect was found to be
a repo-wide pattern, not a portal-api-only defect. This section catalogues every
klai FastAPI service's uvicorn launch line AND its identity/source-IP derivation
logic so the v0.3.0 expansion of this SPEC has a concrete inventory to verify
against.

### uvicorn launch inventory

Result of grepping every klai FastAPI service Dockerfile + entrypoint for
`uvicorn` CMD lines and flag usage:

| Service | Launch location | Line | CMD | `--proxy-headers`? | `--forwarded-allow-ips`? |
|---|---|---|---|---|---|
| portal-api | `klai-portal/backend/entrypoint.sh` | 16 | `exec uvicorn app.main:app --host 0.0.0.0 --port 8010 "$@"` | NO | NO |
| retrieval-api | `klai-retrieval-api/Dockerfile` | 15 | `CMD ["uvicorn", "retrieval_api.main:app", "--host", "0.0.0.0", "--port", "8040"]` | NO | NO |
| knowledge-ingest | `klai-knowledge-ingest/Dockerfile` | 41 | `CMD ["uvicorn", "knowledge_ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]` | NO | NO |
| scribe-api | `klai-scribe/scribe-api/Dockerfile` | 25 | `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]` | NO | NO |

Every service is identical in the defect. Per-service severity of the resulting
exposure varies (see next subsection), but the fix pattern is uniform. This
uniformity is the justification for REQ-6 (shared wrapper) -- anything less will
silently regress as services are added.

### Rate-limit / source-IP derivation per service

Where does each service read "the caller's identity" for rate-limiting or
logging? This is the second half of the picture -- fixing uvicorn alone is not
enough if the application layer re-reads `X-Forwarded-For` unconditionally.

**retrieval-api** -- `klai-retrieval-api/retrieval_api/middleware/auth.py`

```python
# L233-245 (verbatim)
def _source_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _rate_limit_key(auth: AuthContext, request: Request) -> str:
    if auth.method == "jwt" and auth.sub:
        return f"retrieval:rl:jwt:{_hash_sub(auth.sub)}"
    return f"retrieval:rl:internal:{_source_ip(request)}"
```

Observations:
- `_source_ip` checks the raw header BEFORE falling back to `request.client.host`.
  This is independent of uvicorn's `--proxy-headers` setting: the header is read
  from `request.headers`, not from ASGI scope's transport info.
- The internal-path rate-limit key (`retrieval:rl:internal:<ip>`) buckets all
  internal-secret-authenticated callers by IP. This is the only knob the limiter
  has for non-JWT traffic -- there is no per-caller service identity.
- Consequence: any klai-net peer that can reach retrieval-api:8040 AND has the
  internal secret (portal-api, litellm knowledge hook, ad-hoc curl from inside
  a misconfigured sidecar) can:
  1. Forge `X-Forwarded-For: 1.2.3.4` and put all its requests under a bucket
     that nobody else uses → bypass the 600 rpm ceiling.
  2. Forge `X-Forwarded-For: <another-tenant's-external-IP>` and collapse that
     tenant's bucket onto itself → denial-of-service amplification.
- Fix per REQ-1.5: drop the header read entirely and rely on
  `request.client.host` post-uvicorn-proxy-headers, OR gate the header read on a
  trusted-proxy allowlist inside `_source_ip`. The former is simpler and cheaper
  -- preferred.

**knowledge-ingest** -- uses `InternalSecretMiddleware` (app-level) for every
non-health request. The middleware at `knowledge_ingest/middleware.py` does not
currently rate-limit or derive a source-IP key (see `projects/knowledge.md`
pitfalls note on the two auth mechanisms). The uvicorn defect is therefore
lower severity today: spoofed XFF lands in structlog context (via
`RequestContextMiddleware`) but does not affect auth or bucket identity. The
fix is still required to prevent the pattern spreading to future rate-limit or
per-caller policies that might be added later -- this is a classic
"defect-in-waiting" (the bypass works; nothing currently reads the value to
act on it).

**scribe-api** -- similar shape to knowledge-ingest. Uses internal-secret auth
(via `InternalSecretMiddleware` equivalent in `scribe-api/app/middleware.py`).
No rate-limit layer reads XFF today. Same "defect-in-waiting" reasoning.

**portal-api** -- covered in the v0.2.0 body above. The defect is in
`_require_webhook_secret` at `meetings.py:46-58` (IP-range early return), which
is the severity-CRITICAL manifestation because the bypass is directly reached by
every external Vexa webhook call today.

### Caddy trust at the edge vs internal-service trust

One correctness subtlety: the portal-api fix trusts Caddy to supply a
real-client XFF. retrieval-api, knowledge-ingest, and scribe-api are NOT
reached through Caddy for their service-to-service traffic. Their TCP peer on
`klai-net` is the originating klai service (portal-api, litellm, etc.), not
Caddy. Therefore:

- For portal-api: `--forwarded-allow-ips=<caddy-ip>` is the correct value. XFF
  means "the real external client IP that Caddy observed".
- For retrieval-api / knowledge-ingest / scribe-api: `--forwarded-allow-ips`
  should be `127.0.0.1` (i.e. "nobody is trusted to set XFF; always use the TCP
  peer"). This is the safer option, and it's aligned with what these services
  actually need -- the rate-limit bucket should be per calling service
  container IP, not per "whatever XFF the caller claims".

The shared wrapper (REQ-6) MUST support both cases via an env var:
`UVICORN_FORWARDED_ALLOW_IPS`. Per service, set appropriately:
- portal-api: `UVICORN_FORWARDED_ALLOW_IPS=${CADDY_CONTAINER_IP}`
- internal services: `UVICORN_FORWARDED_ALLOW_IPS=127.0.0.1`

An empty or unset value MUST fail-closed per REQ-6.3 -- silent fallback to
`127.0.0.1` is forbidden because a future service author could forget to set
the var and accidentally re-create the defect.

### Why this expands this SPEC instead of opening a new one

Option A (considered, rejected): open `SPEC-SEC-XFF-001` as a separate SPEC.

Rejected because:
- The fix is architecturally identical to REQ-1 of this SPEC (uvicorn flags).
- The shared wrapper (REQ-6) is the cleanest fix and is useless if it only
  lands for portal-api -- every service needs to adopt it in one deploy.
- Splitting would produce a dangling SPEC-SEC-WEBHOOK-001 that solves a
  portal-api-shaped version of a repo-wide problem, which is confusing
  locally and worse for future auditors who search for "uvicorn proxy-headers"
  and find only half the fix.

Option B (chosen): expand SPEC-SEC-WEBHOOK-001 scope to cover every klai
FastAPI service's uvicorn launch, rename nothing (the SPEC ID still reads
"WEBHOOK" because the headline finding was webhook-shaped), and explicitly
document the broader scope in v0.3.0 HISTORY and Environment.

### References (v0.3.0 additions)

- [klai-retrieval-api/retrieval_api/middleware/auth.py#L233-L245](../../../klai-retrieval-api/retrieval_api/middleware/auth.py#L233) -- `_source_ip` and `_rate_limit_key`
- [klai-retrieval-api/Dockerfile#L15](../../../klai-retrieval-api/Dockerfile#L15) -- uvicorn CMD without `--proxy-headers`
- [klai-knowledge-ingest/Dockerfile#L41](../../../klai-knowledge-ingest/Dockerfile#L41) -- uvicorn CMD without `--proxy-headers`
- [klai-scribe/scribe-api/Dockerfile#L25](../../../klai-scribe/scribe-api/Dockerfile#L25) -- uvicorn CMD without `--proxy-headers` (ffmpeg install shifts uvicorn line down from 14 to 25 relative to the task-input line number)
