# Klai Tenant-Isolation Standards

**Doel:** dit document inventariseert de bestaande, mooie patterns in klai voor tenant-isolatie. Elke fix uit `report.md` moet een van deze patterns gebruiken — niet opnieuw uitvinden, niet variëren zonder reden.

**Bron-of-truth:** code in deze repo. Online research alleen ter cross-validatie waar pattern niet kant-en-klaar is.

**Doelgroep:** agents die deze nacht de findings uitvoeren. Lees dit eerst.

---

## 1. PostgreSQL RLS — Category-D ("strict") pattern

### Wanneer
Standaard voor elke tenant-tagged tabel BEHALVE pre-auth lookups (zie Cat-A). Failure mode = "fail loud, geen silent empty results".

### Canonieke referentie
- Helper-function: [klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql](../../klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql)
- Migratie-template: [klai-portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py](../../klai-portal/backend/alembic/versions/c5d6e7f8a9b0_add_rls_policies.py)
- Session helpers: [klai-portal/backend/app/core/database.py](../../klai-portal/backend/app/core/database.py)

### Helper-function (één keer, in alle services die RLS adopteren)
```sql
CREATE OR REPLACE FUNCTION _rls_current_org_id()
    RETURNS <int|text>   -- depending on org_id type in this service
    LANGUAGE plpgsql
    STABLE
AS $$
DECLARE
    v_org    text := current_setting('app.current_org_id', true);
    v_bypass text := current_setting('app.cross_org_admin', true);
BEGIN
    IF v_bypass = 'true' THEN
        RETURN NULL;  -- explicit cross-org bypass via cross_org_session()
    END IF;
    IF v_org IS NULL OR v_org = '' THEN
        RAISE EXCEPTION 'tenant context missing — neither app.current_org_id nor app.cross_org_admin set'
            USING ERRCODE = '42501';  -- insufficient_privilege
    END IF;
    RETURN v_org::<int|text>;
END
$$;
ALTER FUNCTION _rls_current_org_id() OWNER TO klai;
```

**Type-discipline:** in `portal-api` (org_id is int) → `RETURNS integer`. In `connector` / `knowledge` / `research` (org_id/tenant_id is text/varchar/uuid) → `RETURNS text` of `RETURNS uuid`. Geen impliciete casts — die hebben we al een keer betaald (zie `db-schema-consistency.md` 2026-05-04).

### Per tabel
```sql
-- 1. Enable + force (force = owner respects too — defense-in-depth)
ALTER TABLE <schema>.<table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <schema>.<table> FORCE ROW LEVEL SECURITY;

-- 2. Single FOR ALL policy with explicit USING + WITH CHECK
CREATE POLICY tenant_isolation ON <schema>.<table>
    FOR ALL
    USING      (org_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL)
    WITH CHECK (org_id = _rls_current_org_id());
```

**Belangrijk:**
- USING heeft `OR _rls_current_org_id() IS NULL` zodat `cross_org_session()` werkt (helper retourneert NULL bij bypass).
- WITH CHECK heeft die OR NIET — een INSERT/UPDATE moet altijd een echt org_id matchen, ook in cross-org sessions. Voorkomt bug-class A-1.

### Junction-tabellen (geen eigen org_id)
Subquery-pattern naar parent:
```sql
CREATE POLICY tenant_isolation ON portal_taxonomy_nodes
    FOR ALL
    USING      (kb_id IN (SELECT id FROM portal_knowledge_bases
                          WHERE org_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL))
    WITH CHECK (kb_id IN (SELECT id FROM portal_knowledge_bases
                          WHERE org_id = _rls_current_org_id()));
```

---

## 2. PostgreSQL RLS — Category-A ("permissive on missing") pattern

### Wanneer
ALLEEN voor tabellen die VÓÓR auth-resolve gelezen worden:
- `portal_users` — `_get_caller_org` zoekt rij op `zitadel_user_id` voordat tenant context bestaat
- `portal_join_requests` — token-approve flow heeft geen geauthenticeerde caller
- `portal_connectors` — internal `/connectors/*` callbacks laden by id voordat org_id bekend is
- Pre-auth widget-config endpoints (`widgets`, `partner_api_keys`)

### Pattern
```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <table> FORCE ROW LEVEL SECURITY;

-- USING permits IS NULL branch (allows pre-auth read).
-- WITH CHECK does NOT permit IS NULL — INSERT/UPDATE always require explicit tenant binding.
CREATE POLICY tenant_isolation ON <table>
    FOR ALL
    USING      (org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
                OR current_setting('app.current_org_id', true) = '')
    WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer);
```

**Voorbeeld:** [klai-portal/backend/alembic/versions/2f7d1eae1198_add_rls_join_requests_and_allowed_domains.py](../../klai-portal/backend/alembic/versions/2f7d1eae1198_add_rls_join_requests_and_allowed_domains.py) (PR #364, canonieke referentie sinds 2026-05-05).

**Anti-pattern (do NOT):** Cat-A zonder expliciete WITH CHECK — Postgres hergebruikt USING dan voor INSERT en de IS-NULL branch laat any-org-INSERT toe (Finding A-1). Altijd expliciete WITH CHECK opnemen.

---

## 3. Sessie-helpers — `set_tenant`, `tenant_scoped_session`, `cross_org_session`

### Canonieke referentie
[klai-portal/backend/app/core/database.py](../../klai-portal/backend/app/core/database.py)

### `set_tenant(session, org_id)`
Eenmalige tenant-context-set, gebruikt door `_get_caller_org` na auth. Pinned connection vereist (anders silent RLS-filter). Vandaag: gebruikt door request-pad.

### `tenant_scoped_session(org_id)`
Voor BackgroundTasks, asyncio create_task callbacks, poller-loops die per tenant werken:
```python
async with tenant_scoped_session(org_id) as db:
    db.add(MyModel(...))
    await db.commit()
```
Pin-and-reset op exit. Pas alleen toe voor SINGLE-tenant operaties.

### `cross_org_session()`
ALLEEN voor admin/reaper/correlation-lookup tasks die door alle tenants moeten kijken:
```python
async with cross_org_session() as db:
    result = await db.execute(select(VexaMeeting).where(VexaMeeting.ical_uid == uid))
    # iCal UID is globally unique → OK to scan cross-org
```
Zet `app.cross_org_admin=true`, `_rls_current_org_id()` retourneert NULL → USING-branch passeert.

**Regels:**
- Nooit voor per-tenant work
- Comment vereist die uitlegt waarom cross-org noodzakelijk is
- INSERT/UPDATE/DELETE binnen `cross_org_session()` is verdacht — `WITH CHECK` zonder NULL-branch blokkeert het. Splits scan en write in twee sessies (Finding C-3).

### Voor connector / knowledge / research (niet portal-api)
Die services hebben deze helpers niet vandaag. **Strategie:** kopieer de pattern uit `klai-portal/backend/app/core/database.py` naar elke service zijn eigen `app/core/database.py` (of `app/db.py`). Geen extractie naar `klai-libs/` voor nu — de helpers leunen op SQLAlchemy types die per-service variëren. Eventuele extractie is een vervolg-SPEC.

---

## 4. Cross-org-by-design markers

### Pattern
Wanneer een site INTENTIONAL cross-org draait:
```python
# cross-org-by-design: <reason — concrete, not "for legacy reasons">
# - Why this scans all orgs: <e.g. iCal UID is globally unique, dedup must scan all tenants>
# - Why no helper: <e.g. cross_org_session() not yet wired in this service>
# - When to revisit: <e.g. when SPEC-SEC-CONNECTOR-RLS-001 lands, replace with cross_org_session()>
```

Of via `cross_org_session()` met inline reason. Zonder een van beide → finding.

### Voorbeelden
- [klai-portal/backend/app/services/bot_poller.py:154-164](../../klai-portal/backend/app/services/bot_poller.py)
- [klai-portal/backend/app/services/recording_cleanup.py:130-138](../../klai-portal/backend/app/services/recording_cleanup.py)
- [klai-portal/backend/app/services/invite_scheduler.py:64-67](../../klai-portal/backend/app/services/invite_scheduler.py)

---

## 5. Pydantic `_require_<X>_secret` validators

### Wanneer
Elke env-var die fail-open consequences heeft als hij leeg is:
- HMAC secrets voor webhooks
- Internal-service tokens
- Encryption keys (zie sectie 7 voor speciale handling)

### Canonieke referentie
[klai-portal/backend/app/core/config.py](../../klai-portal/backend/app/core/config.py) — `_require_vexa_webhook_secret`, `_require_moneybird_webhook_token`.

### Pattern
```python
from pydantic import model_validator

class Settings(BaseSettings):
    foo_secret: str = ""

    @model_validator(mode="after")
    def _require_foo_secret(self) -> "Settings":
        """SPEC-XXX REQ-N: fail-closed on missing foo_secret.

        <Concrete description of what fails if empty.>

        Env-parity: FOO_SECRET must exist in klai-infra/core-01/.env.sops
        BEFORE this validator lands (see pitfall validator-env-parity in
        .claude/rules/klai/pitfalls/process-rules.md).
        """
        if not self.foo_secret or not self.foo_secret.strip():
            raise ValueError(
                "Missing required: FOO_SECRET (SPEC-XXX REQ-N). "
                "Set it in SOPS before starting <service>, or unregister the <feature> router."
            )
        return self
```

**Hard rule (`validator-env-parity`):** vóór mergen — verifieer dat de env-var in `klai-infra/core-01/.env.sops` staat. Anders crash-loop bij deploy.

### Test-pattern
```python
def test_settings_fails_without_foo_secret(monkeypatch):
    monkeypatch.delenv("FOO_SECRET", raising=False)
    with pytest.raises(ValidationError, match="FOO_SECRET"):
        Settings()
```

---

## 6. Webhook-replay nonce store (`klai-libs/webhook-replay`)

### Canonieke referentie
- Library: [klai-libs/webhook-replay/webhook_replay/nonce_store.py](../../klai-libs/webhook-replay/webhook_replay/nonce_store.py)
- Adopter (canonical): [klai-mailer/app/nonce.py](../../klai-mailer/app/nonce.py) (post-extraction, 58 lines compat-wrapper)

### Adoption pattern (Moneybird/Vexa/Gitea)
```python
from webhook_replay import WebhookNonceStore, NonceReplayError, RedisUnavailableError

# Construct once at module scope (or as FastAPI dependency)
_moneybird_nonce_store = WebhookNonceStore(
    redis_url=settings.redis_url,
    prefix="portal:moneybird-nonce:",
    ttl_seconds=300,  # 5 minutes — wider than HMAC replay window
)

# In handler, AFTER HMAC verification:
@router.post("/moneybird")
async def moneybird_webhook(request: Request, payload: MoneybirdPayload):
    if not _verify_signature(payload):
        raise HTTPException(401, detail="invalid_signature")

    # Nonce check — fail-closed on Redis down (security control, not availability)
    try:
        await _moneybird_nonce_store.check_and_record(payload.event_id, payload.timestamp)
    except NonceReplayError:
        logger.warning("moneybird_webhook_replay_blocked", event_id=payload.event_id)
        raise HTTPException(409, detail="replay_blocked")
    except RedisUnavailableError:
        logger.error("moneybird_webhook_redis_down")
        raise HTTPException(503, detail="webhook_replay_protection_unavailable")

    # ... process payload
```

### Nonce-parts conventie per webhook
| Webhook | Parts | Reason |
|---|---|---|
| Mailer Zitadel | `(timestamp, v1_hash)` | Zitadel signs both, replay-window = 300s |
| Moneybird | `(event_id, timestamp)` | Moneybird sends unique event_id per delivery |
| Vexa | `(vexa_meeting_id, status, timestamp)` | Per-meeting per-status replay window |
| Gitea | `(delivery_id,)` | Gitea sends X-Gitea-Delivery header (unique UUID) |

### Anti-pattern
- `if redis_unavailable: pass` → fail-open, attacker met netwerk-positie kan onbeperkt replay
- TTL > 300s → replay-window onnodig groot
- Geen logging op replay-blocked → silent kan een attack-pattern verbergen

---

## 7. Identity-assertion library (`klai-libs/identity-assert`)

### Wanneer
Elk service-to-service endpoint dat tenant-id of user-id uit caller-input neemt. Internal-secret bewijst NETWERK-identity, niet TENANT-identity.

### Canonieke referentie
- Library: [klai-libs/identity-assert/klai_identity_assert/](../../klai-libs/identity-assert/klai_identity_assert/)
- Spec: SPEC-SEC-IDENTITY-ASSERT-001
- Adopters: knowledge-mcp, scribe, retrieval-api `/retrieve` (live), mailer (live)

### Adoption pattern
```python
from klai_identity_assert import IdentityAsserter, IdentityDenied

asserter = IdentityAsserter(
    portal_base_url=settings.portal_base_url,
    internal_secret=settings.internal_secret,
)

@router.post("/some-endpoint")
async def handler(req: SomeRequest, request: Request):
    try:
        result = await asserter.verify(
            caller_service=request.headers.get("X-Caller-Service", ""),
            claimed_user_id=req.user_id,       # if applicable
            claimed_org_id=req.org_id,
            bearer_jwt=request.headers.get("Authorization", "").removeprefix("Bearer "),
            request_headers=dict(request.headers),
        )
    except IdentityDenied:
        raise HTTPException(403, detail="identity_assertion_failed")

    # Use result.user_id / result.org_id (NOT req.user_id / req.org_id) downstream
    ...
```

### Sender-side (van adopter naar receiver)
Header `X-Caller-Service: <name>` waar `<name>` in `klai_identity_assert.KNOWN_CALLER_SERVICES` staat:
```python
headers = get_trace_headers()
headers["X-Caller-Service"] = "portal-api"  # or "scribe", "mailer", etc.
async with httpx.AsyncClient() as client:
    await client.post(url, json=body, headers=headers)
```

**Hard pitfall:** elke nieuwe consumer van `KNOWN_CALLER_SERVICES` MOET een unit-test hebben die `X-Caller-Service: <name>` lockt — anders silent breekt de volgende refactor het. Zie `retrieve-caller-service-header-mismatch` pitfall.

---

## 8. Post-deploy SQL conventie

### Wanneer
Wanneer alembic-migratie als `portal_api` (of analoog service-role) draait maar de DDL `klai` superuser nodig heeft (RLS policies, functies, table-owner-changes).

### Canonieke referentie
- [klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql](../../klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql)
- [klai-portal/backend/alembic/versions/post_deploy_f0a1b2c3d4e5.sql](../../klai-portal/backend/alembic/versions/post_deploy_f0a1b2c3d4e5.sql)

### Pattern
```sql
-- post_deploy_<rev>.sql
-- Run as `klai` superuser. The Alembic migration role (`<service>_api`)
-- cannot CREATE OR REPLACE FUNCTION / ALTER POLICY.
-- Idempotent: safe to re-run.
--
-- <Description of what this migrates and why operator must run it>

BEGIN;

-- ... DDL here ...

COMMIT;
```

### Operator-uitvoering
```bash
# Apply via apply_post_deploy_sql.sh helper:
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < post_deploy_<rev>.sql
```

### SPEC-checklist toevoeging
Elke SPEC die post-deploy SQL toevoegt, MOET in zijn Success Criteria een expliciete operator-step opnemen:
> "After CI deploy completes: `ssh core-01 'docker exec -i klai-core-postgres-1 psql -U klai -d klai' < klai-portal/backend/alembic/versions/post_deploy_<rev>.sql`"

---

## 9. Alembic auto-migrate (`entrypoint.sh`)

### Canonieke referentie
- [klai-portal/backend/entrypoint.sh](../../klai-portal/backend/entrypoint.sh) (canonical)
- [klai-connector/entrypoint.sh](../../klai-connector/entrypoint.sh)
- [klai-scribe/scribe-api/entrypoint.sh](../../klai-scribe/scribe-api/entrypoint.sh)

### Pattern
```bash
#!/bin/sh
set -e

# Run alembic migrations before starting the API.
echo "[entrypoint] Running alembic upgrade head..."
alembic upgrade head

echo "[entrypoint] Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Hard rule** voor klai-connector: `alembic.ini` MOET `prepend_sys_path = .` bevatten (anders `ModuleNotFoundError: No module named 'app'`). Zie pitfall `scribe-deploy-no-alembic`.

Voor knowledge-ingest, mailer, retrieval-api: deze services hebben GEEN auto-migrate vandaag. Wanneer een tenant-isolation SPEC een migratie toevoegt aan een van deze services: ALTIJD entrypoint.sh-pattern toevoegen. Anders schip je de migratie in de image maar past prod het nooit toe (`alembic-stamped-past-skipped-migration` pitfall).

---

## 10. SPEC-AUTH-009 multi-org user resolution

### Probleem (zie A-12)
`portal_users` heeft `UniqueConstraint("zitadel_user_id", "org_id")` — meerdere rijen per multi-org user. Elke service die `WHERE zitadel_user_id = :uid` doet zonder JWT-resourceowner check pakt willekeurige tenant.

### Canonieke aanpak
1. **JWT-resourceowner als bron-van-waarheid:** Zitadel JWT bevat `urn:zitadel:iam:org:project:resourceowner` claim — dat IS de tenant.
2. **Lookup pattern:**
```python
zitadel_user_id = jwt["sub"]
zitadel_org_id  = jwt["urn:zitadel:iam:org:project:resourceowner"]

result = await db.execute(text("""
    SELECT pu.org_id, po.zitadel_org_id
    FROM portal_users pu
    JOIN portal_orgs po ON po.id = pu.org_id
    WHERE pu.zitadel_user_id = :uid
      AND po.zitadel_org_id = :rid
    LIMIT 1
"""), {"uid": zitadel_user_id, "rid": zitadel_org_id})

row = result.fetchone()
if row is None:
    raise HTTPException(403, detail="user_not_in_resourceowner_tenant")
```

3. **Geen fall-back op "eerste rij die matcht op user_id alleen"** — dat is precies de A-12 bug.

### Adoption notes
- `klai-focus/research-api/app/core/auth.py` MUST adopt deze pattern (Finding A-12)
- `klai-scribe/scribe-api` MUST hetzelfde doen wanneer A-9 (org_id toevoeging) landt
- Portal-api auth-flow doet dit al correct via `_get_caller_org`

---

## 11. Qdrant filter-key discipline

### Twee collections, twee keys
| Collection | Tenant-key | Type | Adopter side |
|---|---|---|---|
| `klai_knowledge` | `org_id` | string (Zitadel resourceowner) | knowledge-ingest write, retrieval-api read |
| `klai_focus` | `tenant_id` | string (Zitadel resourceowner) | research-api write, retrieval-api notebook-scope read |

### Hard rules
1. **Elke** `client.search/scroll/retrieve/delete/upsert` op deze collections **MOET** een `Filter(must=[FieldCondition(key="<correct-key>", match=MatchValue(value=<tenant>))])` hebben.
2. Cross-collection key-bug = CRIT (oorzaak van #343).
3. Type-discipline: beide zijn STRINGS (niet int!). De audit-prompt's claim "klai_knowledge.org_id is int" is verouderd — verifieer in code, niet in spec.

### Pattern
```python
# WRITE
await client.upsert(
    collection_name="klai_knowledge",
    points=[
        PointStruct(
            id=...,
            vector=...,
            payload={
                "org_id": str(org.zitadel_org_id),  # ← niet str(org.id)!
                # ... other fields
            },
        )
    ],
)

# READ
results = await client.search(
    collection_name="klai_knowledge",
    query_vector=...,
    query_filter=Filter(must=[
        FieldCondition(key="org_id", match=MatchValue(value=str(org.zitadel_org_id))),
        # ... other conditions
    ]),
    limit=20,
)
```

### Anti-pattern (Finding B-2)
```python
# WRONG: portal_orgs.id is int, klai_knowledge expects Zitadel string
await client.search(query_filter=Filter(must=[
    FieldCondition(key="org_id", match=MatchValue(value=str(org.id))),
]))
```

---

## 12. FalkorDB / Graphiti per-org isolation

### Pattern
**Fysieke graph-isolatie:** `client.select_graph(org_id)` opent een fysiek aparte graph per tenant. Cypher-queries binnen die graph kunnen niet cross-org leaken.

**Daarbovenop:** elke node carry `group_id` property zodat Graphiti's eigen filtering werkt:
```python
graphiti = Graphiti(...)
results = await graphiti.search(query=..., group_ids=[org_id])
```

### Hard rules
1. Nooit `select_graph(...)` met een caller-supplied tenant zonder voorafgaande identity-assertion (zie sectie 7).
2. Graph-naam = `"org_" + str(zitadel_org_id)` of equivalent — geen menging tussen Postgres int IDs en Zitadel strings (`zelfde-string-twee-namespaces` voorkomen).

---

## 13. Garage S3 — auth-proxied vs anonymous

### Huidige zwakte (B-4)
KB-images worden via Caddy `/kb-images/*` ANONIEM geserveerd vanuit Garage website-mode. SHA256 in path is enige barrier — gelekte URL = permanent cross-tenant lees.

### Veilige pattern (te implementeren)
**Optie A (gekozen voor B-4):** auth-proxy via portal-api
```
/kb-images/{org_id}/{kb_slug}/{sha}.{ext}
  → Caddy reverse-proxy → portal-api endpoint
    → portal-api verifies session.user.org_id == path[org_id] OR widget-public-allowlist
    → portal-api streams from Garage S3 API (private bucket, authenticated)
```

**Optie B:** Garage presigned-URL met short TTL
```python
url = garage_client.generate_presigned_url(
    "get_object",
    Params={"Bucket": "klai-images", "Key": object_key},
    ExpiresIn=300,  # 5 minutes
)
```
Werkt ook, maar kost dedup-win (URL elke keer anders, browser cache miss). Bewaar voor public-widget use-case waar hoog throughput telt.

### Industry-standard cross-validatie
- AWS S3 best practice: presigned URLs met TTL ≤ 15 min voor user-genereerde content (https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html — geverifieerd via Context7 indien nodig).
- Auth-proxy pattern: Cloudflare R2 Workers, Backblaze B2 token-auth — consistent met onze keuze.

---

## 14. Redis tenant-prefixing

### Pattern
Elke key carry tenant-component:
```python
# GOED:
key = f"templates:{org.zitadel_org_id}:{user_id}"
key = f"kb_ver:{org.zitadel_org_id}:{user_id}"
key = f"connector_rl:read:{org.zitadel_org_id}"
key = f"identity_verify:{caller_service}:{claimed_user_id}"

# FOUT (Finding B-9):
key = f"fb:{message_id}:{conversation_id}"  # geen tenant
```

### Producer/consumer key-shape consistency
Writer en invalidator MOETEN dezelfde key-shape gebruiken. Vandaag (Finding B-5): LiteLLM-hook schrijft `templates:{zitadel_str}:{user_id}`, portal-api invalidator pattern-deletet `templates:{int}:{user_id}` — silent mismatch, nooit geïnvalideerd.

**Test-pattern:** roundtrip-test die schrijft via writer-helper en invalidate via consumer-helper:
```python
async def test_templates_cache_invalidation_roundtrip():
    org = make_org(id=42, zitadel_org_id="123456")
    await litellm_hook.write_templates_cache(org=org, user_id="u1", templates=[...])
    await portal_invalidator.invalidate_templates(org=org, user_id="u1")
    assert await redis.get(_templates_key(org, "u1")) is None
```

### Deprovisioning flush
Bij tenant-deprovisioning MOET `_flush_redis_tenant_keys` ALLE per-tenant prefixes flushen. Vandaag flusht alleen `configs:{slug}:*` (Finding B-10). Volledige lijst zie standards-sectie van die finding.

---

## 15. MoneyBird/Vexa/Gitea webhook authentication composite

### Drie controles in volgorde
```python
@router.post("/moneybird")
async def moneybird_webhook(request: Request):
    body = await request.body()

    # 1. HMAC signature verification (constant-time)
    if not _verify_hmac_constant_time(body, request.headers.get("X-Moneybird-Signature", "")):
        raise HTTPException(401, detail="invalid_signature")

    # 2. Replay protection (after HMAC — never pollute the cache with attacker noise)
    payload = MoneybirdPayload.model_validate_json(body)
    try:
        await _moneybird_nonce_store.check_and_record(payload.event_id, payload.timestamp)
    except NonceReplayError:
        raise HTTPException(409, detail="replay_blocked")
    except RedisUnavailableError:
        raise HTTPException(503, detail="webhook_replay_protection_unavailable")

    # 3. Tenant resolution from VERIFIED payload (not from URL path / spoofable body field)
    org = await _resolve_org_from_payload(payload)
    if org is None:
        raise HTTPException(404, detail="org_not_found")

    # 4. Process within tenant scope
    async with tenant_scoped_session(org.id) as db:
        await _process_moneybird_event(db, org, payload)

    return {"status": "ok"}
```

### HMAC-conventie
```python
import hmac
import hashlib

def _verify_hmac_constant_time(body: bytes, sig_hex: str) -> bool:
    expected = hmac.new(
        settings.moneybird_webhook_token.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig_hex)
```

**ALTIJD `hmac.compare_digest`**, nooit `==`. Pitfall: `non-constant-time-secret-compare`.

### Gitea-spoof prevention (C-1)
`_get_org_id` MAG NIET `description` field van Gitea-org gebruiken. Vervang door server-side mapping in `knowledge.org_config` of een dedicated `knowledge.gitea_repo_to_org` tabel:
```sql
CREATE TABLE knowledge.gitea_repo_to_org (
    full_name TEXT PRIMARY KEY,        -- e.g. "org-acme/handbook"
    org_id    TEXT NOT NULL,           -- Zitadel resourceowner string
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE knowledge.gitea_repo_to_org ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.gitea_repo_to_org FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON knowledge.gitea_repo_to_org
    FOR ALL
    USING      (org_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL)
    WITH CHECK (org_id = _rls_current_org_id());
```

Beheerd via portal-api admin endpoint of automatisch bij connector-creation.

---

## 16. Platform-admin gating

### Pattern
```python
from app.api.admin._helpers import _require_admin, _require_platform_admin

@router.post("/admin/orgs/{slug}/some-cross-tenant-action")
async def some_action(
    slug: str,
    caller_user: PortalUser = Depends(_get_caller_user),
    _caller_org: PortalOrg = Depends(_get_caller_org),
):
    _require_admin(caller_user)
    _require_platform_admin(_caller_org)   # ← MUST for cross-tenant endpoints

    # ... operate on a different tenant's data
```

### Hard rule
Elk admin-endpoint dat:
- Een `slug` URL-parameter neemt EN
- Iets doet dat een ANDERE tenant raakt dan caller's eigen org

MOET `_require_platform_admin(_caller_org)` aanroepen. Zonder deze check = Finding C-2 class.

### Audit-log
Elke platform-admin actie MOET in `tenant_lifecycle_events` of `portal_audit_log` met:
- `actor_user_id` (caller)
- `target_org_id` (de andere tenant)
- `action` (e.g. `"retry_provisioning"`)
- `correlation_id` (van request headers)

---

## 17. Conventional commits + PR-pattern voor deze nacht

### Branch-naming
- `feature/SPEC-TI-RLS-CONNECTOR` (per cluster)
- `fix/audit-tenant-isolation/<finding-id>` (single-finding fixes)

### Commit-format
```
<type>(<scope>): <subject>

<body explaining WHY, referencing finding-id>

Refs: SPEC-TI-XXX
Finding: <C-2 / A-7 / B-1>
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### PR-format
```markdown
## Tenant-isolation finding fix: <finding-id> — <title>

**Audit reference:** `reports/audit-tenant-isolation-2026-05-05/report.md` finding <id>

**Pattern:** [section <n>] of `reports/audit-tenant-isolation-2026-05-05/standards.md`

**Changes:**
- ...

**Operator-step (post-deploy):**
- ...

**Tests added:**
- ...

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

### Hard rule — geen direct push to main
Elke wijziging via worktree → branch → PR. Reviewer = user (morgen). Voor CRIT C-2 labelen we PR `urgent` zodat hij top-of-stack zit.

---

## 18. Test-pattern — RLS regression test

### Per nieuwe RLS rollout
```python
# tests/test_<schema>_rls.py
import pytest
from sqlalchemy import select
from app.models.<model> import MyModel
from app.core.database import AsyncSessionLocal, set_tenant, tenant_scoped_session

@pytest.mark.asyncio
async def test_rls_enforces_tenant_isolation():
    """Een SELECT zonder tenant-context raakt _rls_current_org_id() en raise't."""
    async with AsyncSessionLocal() as db:
        # GEEN set_tenant — moet exception triggern
        with pytest.raises(Exception, match="tenant context missing"):
            await db.execute(select(MyModel))

@pytest.mark.asyncio
async def test_rls_filters_by_org_id():
    """Met tenant-context retourneert alleen eigen rijen."""
    org_a, org_b = await _make_two_orgs()
    await _seed(MyModel(org_id=org_a, ...))
    await _seed(MyModel(org_id=org_b, ...))

    async with tenant_scoped_session(org_a) as db:
        rows = (await db.execute(select(MyModel))).scalars().all()
        assert all(r.org_id == org_a for r in rows)
        assert len(rows) == 1
```

---

## 19. Industry-standard cross-validatie (waar nodig)

| Vraag | Antwoord (klai-keuze) | Cross-validatie |
|---|---|---|
| Multi-tenant Postgres pattern? | RLS Cat-D met fail-loud helper-function | Industry-standard: Citus / Crunchy / AWS Aurora multi-tenant guide. Onze fail-loud variant is sterker dan default-empty. |
| HMAC replay-window? | 300s | OWASP webhook-security recommendation: 5 min. Match. |
| S3 presigned-URL TTL? | 5 min waar gebruikt | AWS guidance: ≤ 15 min voor user-genereerde content; 5 min is conservatief. |
| Webhook signature algoritme? | HMAC-SHA256 | OWASP standard. Niet HMAC-SHA1 (collision-resistance issues). |
| Constant-time secret compare? | `hmac.compare_digest` | Python stdlib canonical. |
| Tenant-key in Redis? | Per-key prefix met Zitadel string | Redis multi-tenant best practice (Redis Labs whitepaper). Match. |

Geen vraag gevonden waar onze keuze afwijkt van industry-standard. Alle patterns hierboven kunnen we met vertrouwen toepassen.

---

## 20. Hoe deze nacht op te leveren

### Voor agents (per cluster):
1. Lees deze standards-doc
2. Lees de relevante finding(s) in `report.md`
3. Maak worktree: `git worktree add ../klai-<cluster> -b feature/SPEC-TI-<CLUSTER> main`
4. Implementeer met de patterns hierboven
5. Tests toevoegen (zie sectie 18)
6. Open PR met de PR-format uit sectie 17

### Voor synthesizer (mij):
- Track per-cluster status in TodoWrite
- Bij conflicts of ambiguity → label PR `[DRAFT]`, document wat onduidelijk was, ga verder met volgende cluster
- Eind-rapport naar `reports/audit-tenant-isolation-2026-05-05/RESULTS.md` morgen

---

**Geldigheid:** dit document is bron-van-waarheid voor de 2026-05-05/06 fix-cyclus. Updates aan de patterns hierboven gaan via aparte SPEC.
