---
id: SPEC-INFRA-001
document: acceptance
version: "2.0.0"
---

# SPEC-INFRA-001: Acceptatiecriteria

## AC1: getklai tenant heeft Twenty CRM tools in chat

**Requirement:** REQ-E-001, REQ-E-002

```gherkin
Scenario: Twenty CRM MCP-tools beschikbaar voor getklai tenant
  Given de PortalOrg met slug "getklai" heeft mcp_servers JSON met "twenty-crm" configuratie
    And provisioning heeft een per-tenant librechat.yaml gegenereerd met de gemergede MCP config
    And TWENTY_API_KEY en TWENTY_BASE_URL zijn ingesteld in de container .env
  When een gebruiker in de getklai tenant een nieuwe chat opent
  Then zijn de Twenty CRM MCP-tools beschikbaar in de tool-selectie
    And de klai-knowledge MCP-tools zijn ook beschikbaar
```

**Verificatie:**
1. `SELECT mcp_servers FROM portal_orgs WHERE slug = 'getklai'` bevat "twenty-crm" key
2. `deploy/librechat/getklai/librechat.yaml` bevat `twenty-crm` in `mcpServers` en in `modelSpecs.list[].mcpServers`
3. Chat op getklai.getklai.com toont Twenty CRM tools

---

## AC2: Andere tenants hebben GEEN Twenty CRM tools

**Requirement:** REQ-E-003, REQ-N-001, REQ-N-002

```gherkin
Scenario: Twenty CRM tools niet beschikbaar voor klai tenant
  Given de PortalOrg met slug "klai" heeft mcp_servers = NULL
    And de gegenereerde librechat.yaml bevat alleen klai-knowledge
  When een gebruiker in de klai tenant een nieuwe chat opent
  Then zijn de Twenty CRM MCP-tools NIET beschikbaar
    And de container environment bevat GEEN TWENTY_API_KEY variabele
```

**Verificatie:**
1. `docker exec librechat-klai printenv TWENTY_API_KEY` retourneert leeg
2. Chat op chat.getklai.com toont GEEN Twenty CRM tools

---

## AC3: Yaml generatie merged base + DB config correct

**Requirement:** REQ-E-001

```gherkin
Scenario: Per-tenant yaml bevat base config + tenant MCP servers
  Given de base librechat.yaml bevat klai-knowledge MCP server
    And PortalOrg.mcp_servers bevat {"twenty-crm": {...}}
  When _generate_librechat_yaml() wordt aangeroepen
  Then bevat de output yaml zowel klai-knowledge als twenty-crm in mcpServers
    And bevat modelSpecs.list[0].mcpServers beide servernamen
    And zijn alle andere secties (endpoints, webSearch, etc.) identiek aan de base

Scenario: Tenant zonder extra MCP servers krijgt base config
  Given PortalOrg.mcp_servers is NULL
  When _generate_librechat_yaml() wordt aangeroepen
  Then is de output yaml identiek aan de base config (alleen klai-knowledge)
```

---

## AC4: KNOWLEDGE_INGEST_SECRET correct doorgegeven

**Requirement:** REQ-E-004

```gherkin
Scenario: X-Internal-Secret header bevat KNOWLEDGE_INGEST_SECRET
  Given docker-compose.yml bevat KNOWLEDGE_INGEST_SECRET in de environment van beide LibreChat services
    And /opt/klai/.env bevat een geldige KNOWLEDGE_INGEST_SECRET waarde
  When een gebruiker een klai-knowledge MCP-tool aanroept
  Then bevat de X-Internal-Secret header de correcte waarde
    And accepteert de klai-knowledge-mcp server het request
```

**Verificatie:**
1. `docker exec librechat-klai printenv KNOWLEDGE_INGEST_SECRET` — niet leeg
2. `docker exec librechat-getklai printenv KNOWLEDGE_INGEST_SECRET` — niet leeg

---

## AC5: Nieuwe tenants krijgen KNOWLEDGE_INGEST_SECRET + per-tenant yaml

**Requirement:** REQ-E-005

```gherkin
Scenario: Provisioning genereert .env met KNOWLEDGE_INGEST_SECRET en per-tenant yaml
  Given provisioning.py is bijgewerkt
    And de applicatie-configuratie bevat een geldige knowledge_ingest_secret
  When een beheerder een nieuwe tenant aanmaakt
  Then bevat het gegenereerde .env bestand KNOWLEDGE_INGEST_SECRET=<waarde>
    And bestaat er een per-tenant librechat.yaml in librechat/{slug}/librechat.yaml
    And is de per-tenant yaml gemount in de container
```

---

## Definition of Done

- [ ] `PortalOrg.mcp_servers` JSON kolom bestaat (migratie uitgevoerd)
- [ ] getklai org heeft Twenty CRM config in `mcp_servers`
- [ ] `_generate_librechat_yaml()` functie in provisioning.py
- [ ] Per-tenant yaml wordt gegenereerd en gemount in containers
- [ ] `KNOWLEDGE_INGEST_SECRET` in docker-compose voor pre-provisioned tenants
- [ ] `KNOWLEDGE_INGEST_SECRET` in provisioning.py .env template
- [ ] Server-side: `TWENTY_API_KEY` en `TWENTY_BASE_URL` in getklai .env
- [ ] Alle 5 acceptatiecriteria (AC1-AC5) geverifieerd
- [ ] Geen credentials in git of database
