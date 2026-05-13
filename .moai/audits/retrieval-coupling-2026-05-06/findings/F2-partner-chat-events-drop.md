# F2 — partner_chat dropt `knowledge.queried` events

**Severity:** LOW (functioneel oké, telemetrie verlies)
**Status:** OPEN — needs verification

## Initial finding

[`klai-portal/backend/app/services/partner_chat.py:108-116`](../../../klai-portal/backend/app/services/partner_chat.py#L108-L116) bouwt /retrieve body:

```python
retrieve_body: dict = {
    "query": query,
    "org_id": zitadel_org_id,
    "scope": "org",
    "top_k": 8,
    "conversation_history": conversation_history,
}
```

Geen `user_id`. Dat is voor `scope=org` toegestaan (geen 400). Maar in [`auth.py:514-523`](../../../klai-retrieval-api/retrieval_api/middleware/auth.py#L514-L523) (internal-secret pad):

```python
# auth.method == "internal" — REQ-4.2 portal-side verification.
if body_user_id is None:
    return                       # ← geen verified_caller pinned
```

En [`retrieve.py:411-433`](../../../klai-retrieval-api/retrieval_api/api/retrieve.py#L411-L433):

```python
if req.scope != "notebook":
    verified = getattr(request.state, "verified_caller", None)
    if verified is not None:
        emit_event("knowledge.queried", ...)
    else:
        logger.warning("product_event_skipped_no_identity", ...)
```

Resultaat: `knowledge.queried` events vanuit partner-API verkeer worden gedropt. Logs tonen `product_event_skipped_no_identity` warnings.

## Wired-up confirm

`retrieve_context` wordt aangeroepen vanuit `klai-portal/backend/app/api/partner.py:185`. Dus actief.

## Open vragen voor verificatie

1. Komen er werkelijk `product_event_skipped_no_identity` warnings in VictoriaLogs voor `service:retrieval-api`? Hoeveel per dag?
2. Hoeveel partner-API calls per dag draaien? Quantificeren wat we missen.
3. Is `knowledge.queried` voor partner verkeer überhaupt gewenst? Of is het bewust apart gehouden van eindgebruiker-events?
4. Heeft de partner-API een eindgebruiker concept of zijn alle calls "tenant-level"? Als tenant-level, is `user_id=None` correct en moeten we juist een ander event-type emitteren (bijv. `partner.knowledge_queried`)?

## Voorgestelde fix opties (voor agent te valideren)

**Optie A — fix het event:** geef partner_chat een synthetisch user_id (bijv. `f"partner:{partner_app_id}"`), zodat de event-emit doorloopt en in dashboards te onderscheiden is.

**Optie B — accept het skipt:** documenteer dat partner-API events bewust niet in `knowledge.queried` komen; dempen van de warning omdat het een verwachte staat is voor partner-flows.

**Optie C — apart event:** `partner.knowledge_queried` event met tenant-level data, los van `knowledge.queried`.

## Verification

### Code-trace — CONFIRMED

Alle drie call sites bevestigd op de in de finding genoemde regels:

1. **partner_chat.retrieve_context body** — `klai-portal/backend/app/services/partner_chat.py:108-116` bouwt:
   ```python
   retrieve_body: dict = {
       "query": query,
       "org_id": zitadel_org_id,
       "scope": "org",
       "top_k": 8,
       "conversation_history": conversation_history,
   }
   ```
   Geen `user_id` veld. Header set is `X-Internal-Secret` + `X-Caller-Service: portal-api` (regel 130-140).

2. **retrieval-api auth.py:514-523** — internal-secret pad:
   ```python
   # auth.method == "internal" — REQ-4.2 portal-side verification.
   if body_user_id is None:
       # Bodies that don't carry an end-user claim ... return
       return
   ```
   Bevestigd: `verified_caller` blijft unpinned. De NOTE comment in de code zelf zegt: "hitting this branch means a different surface — leave the verified tuple unpinned and let downstream raise if it tries to read it."

3. **retrieval-api retrieve.py:411-433** — knowledge.queried emit:
   ```python
   if req.scope != "notebook":
       verified = getattr(request.state, "verified_caller", None)
       if verified is not None:
           emit_event("knowledge.queried", ...)
       else:
           # Defense in depth: should be unreachable ...
           logger.warning("product_event_skipped_no_identity", ...)
   ```
   Bevestigd: zonder verified pin, geen emit, alleen warning. De code-comment zegt expliciet "should be unreachable" — dat is feitelijk niet waar voor de partner-API code path.

### Partner-API auth context — CONFIRMED (NOT-A-BUG-BUT-DESIGN)

`klai-portal/backend/app/api/partner.py` gebruikt `PartnerAuthContext` (key-based Bearer auth). Er is **geen end-user identity** in de partner request — het is een tenant+key-bearer model. Belangrijke design-keuze in SPEC-API-001:

- **spec.md regel 66 (Assumptions):** _"Partners use their own user/session management; the Partner API has no concept of end users"_
- **spec.md regel 77 (Out of Scope):** _"Per-end-user authentication within the partner's scope — partners operate at org level only"_
- **partner.py:205** gebruikt al de patroon `user_id=f"partner:{auth.key_id}"` voor `write_retrieval_log` — bewijst dat synthetic-id voor partner-context al een geaccepteerd convention is in dezelfde file.

Conclusie: dit is geen ontwerp-omissie maar een onbedoelde lacune toen SPEC-SEC-IDENTITY-ASSERT-001 Phase D (2026-04-28) `verified_caller`-pinning toevoegde — partner-traffic mist een synthetic identity en valt door de "should be unreachable" else-branch.

### Production evidence — PARTIALLY CONFIRMED (geen huidig verkeer)

Drie observaties uit prod:

- `ssh core-01 "docker logs --since 7d klai-core-retrieval-api-1 | grep -c product_event_skipped_no_identity"` → **0**
- `ssh core-01 "docker logs --since 7d klai-core-portal-api-1 | grep -c partner_chat"` → **0**
- `SELECT COUNT(*) FROM partner_api_keys` → **0**

Partner-API draait maar wordt niet gebruikt in productie — er bestaat geen enkele partner key. Dit is daarom een **latente bug**: hij wordt pas zichtbaar zodra de eerste partner-tenant integreert.

Wel zichtbaar: 34 historische `knowledge.queried` events met `user_id=NULL` (allemaal `org_id=1`, allemaal voor 2026-04-22). Die zijn van vóór SPEC-SEC-IDENTITY-ASSERT-001 Phase D landde — andere oorzaak (LiteLLM hook of gap_rescorer pre-guard) en niet relevant voor deze finding.

### Design alternatief check — geen apart event-type bestaat

`grep -rn "partner.knowledge_queried"` over alle services + SPECs → 0 hits. Er is geen ander telemetrie-kanaal waarin partner-RAG calls worden bijgehouden. Als de scenario activeert worden ze gewoon stilletjes gemist in de Knowledge-dashboards.

## Recommended fix

**Optie A (synthetic user_id) — RECOMMENDED.**

Wijziging in `klai-portal/backend/app/services/partner_chat.py:108-116`: voeg `"user_id": f"partner:{key_id}"` toe aan de retrieve body, met `key_id` als nieuwe parameter op `retrieve_context`. Caller (`partner.py:185`) geeft `auth.key_id` mee.

Daarmee:
1. retrieval-api `auth.py:515` valt niet meer in de unpinned-branch want `body_user_id` is niet `None`.
2. Het volgt het portal-side `KNOWN_CALLER_SERVICES` allowlist pad in `auth.py:546+` (asserter.verify) — maar wacht, dat vereist dat portal-api's `/internal/identity/verify` endpoint `partner:<key_id>` accepteert.

**Belangrijk implementatie-detail (nog te valideren):** synthetic `user_id` moet de portal-side identity assertion doorstaan. Dat vraagt een mini-extension: portal-api `/internal/identity/verify` moet `claimed_user_id="partner:<key_id>"` als geldig accepteren mits er een actieve `partner_api_keys` row bestaat met dat key_id en `org_id == claimed_org_id`. Zonder die uitbreiding kantelt fix A naar een 403 op iedere partner /retrieve. Dit is werk maar consistent met REQ-4.2's intent (portal verifieert end-user identiteit; "partner:<key_id>" is een geldige tenant-bearer identiteit voor partner traffic).

**Eenvoudiger sub-variant — RECOMMENDED ALS EERSTE STAP:** sla portal-verify over voor synthetic `partner:*` user_ids door retrieval-api `auth.py:514-523` te wijzigen naar:

```python
if body_user_id is not None and str(body_user_id).startswith("partner:"):
    # Partner API operates at org-level only (SPEC-API-001 Out-of-Scope:
    # "no concept of end users"). Pin org without round-trip to portal —
    # the X-Internal-Secret + X-Caller-Service still gates the call.
    request.state.verified_caller = VerifiedCaller(
        user_id=str(body_user_id), org_id=str(body_org_id)
    )
    return
```

Dat is contained, uitlegbaar, en consistent met het bestaande `partner:<key_id>` pattern in `partner.py:205` voor `write_retrieval_log`.

**Waarom niet B (accept-the-skip):** dashboards in SPEC-GRAFANA-METRICS REQ ("Knowledge: Queries Per Week", "Knowledge: Query Success Rate") zouden partner-traffic missen. Op tenant-billing impact: partner-keys staan los van LiteLLM team-key billing, dus het ontbreken van `knowledge.queried` events betekent dat een partner-tenant geen RAG-aktivieit toont in de Klai admin-portal — dat is precies de blast-area waar deze events voor zijn gemaakt.

**Waarom niet C (apart event-type):** `partner.knowledge_queried` zou requireren dat élke dashboard query (Knowledge: Queries Per Week, etc.) UNION ALL twee event-types. SPEC-GRAFANA-METRICS noemt nergens `partner.*` events. Voegt onderhoudslast toe aan iedere toekomstige knowledge-dashboard zonder duidelijke baten — partner-traffic is functioneel hetzelfde RAG-event als LiteLLM-traffic, alleen met andere actor.

**Net-out:** Optie A met de `partner:` prefix sub-variant. Eén commit, één extra clausule in retrieval-api auth, één parameter-doorgift in partner_chat. Test: één unit test in `klai-retrieval-api/tests/test_identity_assert.py` die asserts dat `body_user_id="partner:foo"` → `verified_caller` gezet → `knowledge.queried` event geëmit.

## Risk if not fixed

**Latent maar verwacht.** Productie heeft nu nul partner keys, dus het probleem manifest zich niet. Zodra de eerste partner-integratie live gaat:

- **Telemetrie loss:** elke partner /chat/completions request mist een `knowledge.queried` event. Klai admin-portal Knowledge-dashboards (Queries Per Week, Query Success Rate) tonen partner-traffic niet → onmogelijk om partner-tenant adoptie of retrieval-quality te volgen.
- **Debugging gap:** een partner die meldt "RAG werkt niet" kan niet worden bevestigd via product_events. `request_id`-correlatie via VictoriaLogs blijft werken (logs are unaffected) — dus traceable, maar niet via de canonical product-events tabel.
- **Audit trail incompleteness:** SPEC-SEC-IDENTITY-ASSERT-001 ontwerp-intent ("verifieerde identiteit als enkele bron voor business-metrics") wordt voor partner-traffic niet ingelost. De `product_event_skipped_no_identity` warning zal in logs verschijnen voor élke partner request, wat het karakter heeft van false-positive ruis omdat het ontwerp-bedoeld is dat partner geen end-user identity heeft.
- **Severity bevestigd LOW:** geen functionele regressie in chat-output, geen security-impact, geen data corruption. Telemetrie-only. Maar uitstel is goedkoop nu (geen echt verkeer) en duurder later (eerste partner-launch + dashboard-fix tegelijkertijd is meer risk dan nu in een rustig venster).

**Aanbevolen status:** Optie A sub-variant landen vóór de eerste partner-tenant launches. Geen rush, geen prod-incident, maar niet vergeten.
