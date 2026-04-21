# Research: SPEC-SEC-011 — Knowledge-Ingest Fail-Closed Auth

## 1. Source Findings

This SPEC consolidates two audit findings from Phase 3 of the Klai security audit, conducted 2026-04-19.

### F-003 — Middleware fail-open

Documented in `.moai/audit/04-tenant-isolation.md` (section "F-003"). Location: `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py:19-21`.

Relevant excerpt from audit:

> Nieuwe deploy die per ongeluk `KNOWLEDGE_INGEST_SECRET` niet zet = service volledig open. Ook: test-config met lege var kan per ongeluk op prod belanden.

Current code:

```python
class InternalSecretMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip auth if secret is not configured (gradual rollout)
        if not settings.knowledge_ingest_secret:
            return await call_next(request)
        # ... rest of middleware
```

The fail-open branch was introduced as a "gradual rollout" convenience. The audit classifies this as HIGH severity because a single missing env var silently disables auth across the entire service.

### F-012 — Route-level helper ALSO fail-open

Documented in `.moai/audit/04-2-query-inventory.md` (section "F-012"). Location: `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:54-60`.

The same fail-open pattern is duplicated inside the per-route guard helper `_verify_internal_secret`:

```python
def _verify_internal_secret(request: Request) -> None:
    """Verify X-Internal-Secret header for service-to-service calls."""
    if not settings.knowledge_ingest_secret:
        return  # <-- identical fail-open
    secret = request.headers.get("x-internal-secret", "")
    if not hmac.compare_digest(secret, settings.knowledge_ingest_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
```

The audit observation is that when two independent defense layers share the same failure mode, they provide no additional defense. Both layers collapse together on an empty secret.

The audit also notes a positive: the header comparison already uses `hmac.compare_digest`, so timing-safe comparison is preserved once the fail-open branch is removed.

### Roadmap entry

`.moai/audit/99-fix-roadmap.md`, section "SEC-011 — Knowledge-ingest fail-closed auth", captures the fix scope:

> 1. `knowledge_ingest/config.py` — add `model_validator(mode="after")` dat crasht bij lege `knowledge_ingest_secret`
> 2. `knowledge_ingest/middleware/auth.py` — verwijder de fail-open guard (lines 19-21); secret is nu altijd gezet
> 3. `knowledge_ingest/routes/ingest.py:54-60` — idem: verwijder de `if not settings.knowledge_ingest_secret: return` branch
> 4. Check alle andere routes in `knowledge_ingest/routes/*.py` op hetzelfde patroon

Priority: P1 / HIGH. Fix-effort flagged as "extreem klein — 3 regels verwijderen, 1 validator toevoegen". Blast radius: deploys with an empty env var will now crash at startup — the audit explicitly notes this is desirable ("wil je weten").

---

## 2. Two-Layer Auth Pattern in knowledge-ingest

knowledge-ingest implements defense in depth with two complementary auth layers, both gated by the same `settings.knowledge_ingest_secret`:

### Layer 1 — `InternalSecretMiddleware`

Applied app-wide in `knowledge_ingest/main.py` (added via `app.add_middleware(...)`). Intercepts every incoming request before any route handler runs. Short-circuits with 401 on missing / invalid header. Has a hard-coded exemption for `/health` to keep Docker liveness probes and monitoring healthy.

### Layer 2 — `_verify_internal_secret()` in `routes/ingest.py`

Called as the first statement of six specific route handlers:

- `DELETE /ingest/v1/kb` (`delete_kb_route`, line 690)
- `DELETE /ingest/v1/connector` (`delete_connector_route`, line 704)
- `PATCH /ingest/v1/kb/visibility` (`update_kb_visibility_route`, line 729)
- `POST /ingest/v1/kb/webhook` (`register_kb_webhook`, line 818)
- `DELETE /ingest/v1/kb/webhook` (`deregister_kb_webhook`, line 828)
- `POST /ingest/v1/kb/sync` (`bulk_sync_kb_route`, line 838)

These are specifically the destructive / mutating service-to-service operations (KB deletion, webhook lifecycle, bulk re-sync). The double guard is intentional: even if the middleware is misconfigured or bypassed (e.g., future refactor adds a route below a different middleware), the per-route helper still enforces auth. In principle this is a good defense-in-depth pattern; in practice it is defeated because both layers use the identical fail-open branch.

### Other routes do not call the helper

Routes in other modules (`routes/crawl.py`, `routes/knowledge.py`, `routes/personal.py`, `routes/stats.py`) rely exclusively on Layer 1. The middleware-only protection is still sufficient after the fix because:

- The middleware covers all paths by default.
- The fail-open branch is removed by REQ-2.
- Once the secret is required at startup (REQ-1), the middleware has no degraded mode.

Grep verification (performed during research):

```
grep -rn "knowledge_ingest_secret\|_verify_internal_secret" klai-knowledge-ingest/knowledge_ingest/routes/
```

Result (abridged):

- `routes/ingest.py` — defines and calls `_verify_internal_secret` (7 callsites including the definition).
- `routes/taxonomy.py` — defines and calls `_verify_internal_token` (different direction: ingest → portal, gated by `settings.portal_internal_token`, not `knowledge_ingest_secret`). Out of scope per this SPEC.
- `routes/crawl.py`, `routes/knowledge.py`, `routes/personal.py`, `routes/stats.py` — no matches.

---

## 3. Documented Pitfall — Knowledge Domain Rules

The project already documents the two-layer pattern and its failure modes in `.claude/rules/klai/projects/knowledge.md`, under the heading "Portal→ingest auth header: always X-Internal-Secret (HIGH)":

> knowledge-ingest has two separate auth mechanisms that look similar:
> 1. `InternalSecretMiddleware` (app-level) — checks `X-Internal-Secret` on every request. Used for portal→ingest calls.
> 2. `_verify_internal_token()` (per-route helper) — checks `x-internal-token`. Used for ingest→portal calls.

Important distinction: the rule page describes two different headers for two different directions of service-to-service traffic. SPEC-SEC-011 only concerns the first (`X-Internal-Secret` / `knowledge_ingest_secret`). The second (`x-internal-token` / `portal_internal_token`, defined in `routes/taxonomy.py`) is a separate auth surface and is explicitly out of scope; any analogous fail-open branch there is a separate finding.

The pitfall rule further notes that auth bugs in this area tend to cause silent 401s rather than crashes, especially in fire-and-forget outbound calls. That asymmetry is relevant to the current SPEC: the fail-open behavior we are removing causes requests to silently succeed — the opposite of a noisy-failure 401 — which is worse for incident response because misconfigurations cannot be detected from symptoms alone. Making the service fail fast at startup surfaces the misconfiguration immediately.

---

## 4. Callers and Blast Radius

Known callers of knowledge-ingest's protected routes:

| Caller | Module | Routes used | Header set? |
|---|---|---|---|
| portal-api | `app/services/*` (KB lifecycle, connector lifecycle) | `DELETE /ingest/v1/kb`, `DELETE /ingest/v1/connector`, `PATCH /ingest/v1/kb/visibility`, `POST /ingest/v1/kb/webhook`, `DELETE /ingest/v1/kb/webhook`, `POST /ingest/v1/kb/sync` | Yes (via `X-Internal-Secret` header, production-confirmed) |
| klai-connector | sync pipeline | `POST /ingest/v1/document` | Yes |
| klai-knowledge-mcp | MCP tool for LibreChat | `POST /ingest/v1/document`, `DELETE /knowledge/v1/personal/items/*` | Yes |
| Operator ad-hoc | `curl` or deploy scripts | varies | Expected to provide header |

No caller is expected to break from this SPEC: they all already send the header in production. The only observable effect is that a deploy with a missing env var now crashes rather than silently running unauthenticated.

---

## 5. Configuration Surface

Relevant field in `knowledge_ingest/config.py`:

```python
knowledge_ingest_secret: str = ""  # X-Internal-Secret for service-to-service auth
```

After this SPEC:

```python
knowledge_ingest_secret: str = ""  # X-Internal-Secret for service-to-service auth; validated required at init

@model_validator(mode="after")
def _require_secret(self) -> "Settings":
    if not self.knowledge_ingest_secret:
        raise ValueError("KNOWLEDGE_INGEST_SECRET must be set")
    return self
```

Default `= ""` is retained intentionally so that the validator — not the pydantic "field required" error — produces the failure message. The resulting error message names the env var explicitly, which is more actionable in ops incidents.

Production secret source: SOPS-encrypted `klai-infra/core-01/*.sops`, decrypted into container env at boot. No change to this pipeline.

---

## 6. Test Strategy References

The existing `klai-knowledge-ingest/tests/` directory uses pytest + httpx `AsyncClient` for route-level tests and imports modules with `monkeypatch.setenv` in `conftest.py` fixtures. The new `test_middleware_auth.py` follows this convention. For route-level 401 tests, downstream calls (`pg_store`, `qdrant_store`, `graph_module`) are mocked via `unittest.mock.AsyncMock` to keep the test scope purely on the auth layer.

---

## 7. References

- `.moai/audit/04-tenant-isolation.md` — F-003 (middleware)
- `.moai/audit/04-2-query-inventory.md` — F-012 (route helper)
- `.moai/audit/99-fix-roadmap.md` — SEC-011 entry
- `.claude/rules/klai/projects/knowledge.md` — two-layer auth pitfall documentation
- `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py:19-21` — F-003 location
- `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:54-60` — F-012 location
- `klai-knowledge-ingest/knowledge_ingest/config.py` — `Settings` class with `knowledge_ingest_secret`
