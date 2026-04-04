---
id: SPEC-INFRA-001
document: plan
version: "2.0.0"
---

# SPEC-INFRA-001: Implementatieplan

## Overzicht

DB-driven per-tenant MCP configuratie voor LibreChat. De `PortalOrg` tabel krijgt een `mcp_servers` JSON kolom. Provisioning genereert per-tenant `librechat.yaml` door de base config te mergen met de tenant-specifieke MCP servers uit de database.

---

## Taakdecompositie

### T1: Database — `mcp_servers` kolom + Alembic migratie [Priority High]

**Bestanden:**
- `klai-portal/backend/app/models/portal.py` — voeg `mcp_servers` kolom toe
- `klai-portal/backend/alembic/versions/<hash>_add_mcp_servers_to_portal_orgs.py` — migratie

**Stappen:**
1. Voeg toe aan `PortalOrg`: `mcp_servers: Mapped[dict | None] = mapped_column(JSON, nullable=True)`
2. Genereer Alembic migratie: `alembic revision --autogenerate -m "add_mcp_servers_to_portal_orgs"`
3. Voeg data-migratie toe: INSERT de Twenty CRM config voor de `getklai` org (slug = "getklai")

**Data-migratie voor getklai:**
```python
op.execute("""
    UPDATE portal_orgs
    SET mcp_servers = '{"twenty-crm": {"type": "stdio", "command": "npx", "args": ["-y", "twenty-mcp-server", "start"], "timeout": 60000, "initTimeout": 30000, "env": {"TWENTY_API_KEY": "${TWENTY_API_KEY}", "TWENTY_BASE_URL": "${TWENTY_BASE_URL}"}}}'::jsonb
    WHERE slug = 'getklai'
""")
```

**Afhankelijkheden:** Geen.

---

### T2: Provisioning — yaml generatie + `KNOWLEDGE_INGEST_SECRET` [Priority High]

**Bestand:** `klai-portal/backend/app/services/provisioning.py`

**Stappen:**
1. Voeg `_generate_librechat_yaml(base_path, extra_mcp_servers)` functie toe:
   - Laad base yaml met `yaml.safe_load()`
   - Merge `extra_mcp_servers` in `mcpServers` sectie
   - Voeg servernamen toe aan `modelSpecs.list[].mcpServers`
   - Return yaml string via `yaml.dump()`
2. Pas `_start_librechat_container()` aan:
   - Haal `org.mcp_servers` op uit de database
   - Genereer per-tenant yaml en schrijf naar `librechat/{slug}/librechat.yaml`
   - Wijzig volume mount: `f"{librechat_host_base}/{slug}/librechat.yaml"` i.p.v. `f"{librechat_host_base}/librechat.yaml"`
3. Voeg `KNOWLEDGE_INGEST_SECRET={settings.knowledge_ingest_secret}` toe aan `.env` template (na regel 265)

**Afhankelijkheden:** T1 (model moet kolom hebben).

---

### T3: Docker-compose — pre-provisioned tenants [Priority High]

**Bestand:** `deploy/docker-compose.yml`

**Stappen:**
1. `librechat-getklai`: wijzig volume mount naar `./librechat/getklai/librechat.yaml:/app/librechat.yaml:ro`
2. `librechat-getklai`: voeg `KNOWLEDGE_INGEST_SECRET: ${KNOWLEDGE_INGEST_SECRET}` toe aan environment
3. `librechat-klai`: voeg `KNOWLEDGE_INGEST_SECRET: ${KNOWLEDGE_INGEST_SECRET}` toe aan environment

**Afhankelijkheden:** T2 moet de yaml-generatiefunctie bevatten (of we genereren de getklai yaml handmatig als bootstrap).

---

### T4: Bootstrap — genereer getklai yaml + server-side env [Priority High]

**Type:** Eenmalige stap voor bestaande pre-provisioned tenant.

**Stappen:**
1. Voer de yaml-generatiefunctie uit (of maak een management command) om `deploy/librechat/getklai/librechat.yaml` te genereren vanuit base + DB config
2. Op core-01 — voeg toe aan `/opt/klai/librechat/getklai/.env`:
   ```bash
   echo 'TWENTY_API_KEY=<api-key-uit-twenty-crm>' >> /opt/klai/librechat/getklai/.env
   echo 'TWENTY_BASE_URL=https://crm.getklai.com/api' >> /opt/klai/librechat/getklai/.env
   ```
3. Op core-01 — voeg `KNOWLEDGE_INGEST_SECRET` toe als die ontbreekt in `/opt/klai/.env`
4. Deploy en herstart containers

**Afhankelijkheden:** T1 (migratie uitgevoerd), T2 (generatiefunctie), T3 (docker-compose).

---

## Afhankelijkheidsgraaf

```
T1 (DB migratie) ──> T2 (provisioning.py) ──> T4 (bootstrap + deploy)
                                                    ^
T3 (docker-compose) ────────────────────────────────┘
```

T1 en T3 kunnen parallel. T2 hangt af van T1. T4 is de laatste stap.

---

## Risicoanalyse

### R1: npx niet beschikbaar in LibreChat image
**Ernst:** Medium — **Mitigatie:** Verifieer met `docker exec which npx` voor deployment.

### R2: Cold start latency van npx
**Ernst:** Laag — **Mitigatie:** `initTimeout: 30000` in MCP configuratie.

### R3: PyYAML merge overschrijft comments/formatting
**Ernst:** Laag — **Mitigatie:** `yaml.dump()` produceert valide yaml, maar verliest comments uit base. Acceptabel: base yaml comments zijn voor ontwikkelaars, niet voor runtime.

### R4: Bestaande pre-provisioned tenants in docker-compose
**Ernst:** Medium — **Mitigatie:** De `getklai` tenant in docker-compose wordt eenmalig gebootstrapt (T4). Nieuwe tenants gaan automatisch via provisioning.py.

---

## Verificatie na deployment

1. `docker exec librechat-getklai which npx` — npx beschikbaar
2. `docker exec librechat-getklai printenv TWENTY_API_KEY` — niet leeg
3. `docker exec librechat-getklai printenv KNOWLEDGE_INGEST_SECRET` — niet leeg
4. `docker exec librechat-klai printenv KNOWLEDGE_INGEST_SECRET` — niet leeg
5. Open chat in getklai tenant — Twenty CRM tools zichtbaar
6. Open chat in klai tenant — alleen klai-knowledge tools
