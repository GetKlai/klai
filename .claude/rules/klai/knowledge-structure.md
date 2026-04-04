---
paths:
  - ".claude/rules/klai/**"
  - ".claude/agents/klai/**"
  - ".claude/commands/klai/**"
---
# Knowledge Base Structure

> Referentie voor iedereen die nieuwe learnings toevoegt aan `.claude/rules/klai/`.
> Gebruikt door `manager-learn` bij `/retro` en door de sync workflow.

## Structuur

De kennisbank is georganiseerd per domein, niet meer in aparte `patterns/` en `pitfalls/` directories.
Elk bestand bevat zowel patterns als pitfalls voor dat domein, gegroepeerd op onderwerp.

```
.claude/rules/klai/
├── confidence.md              # Confidence protocol (altijd geladen)
├── serena.md                  # Serena MCP integration (altijd geladen)
├── pitfalls/
│   └── process-rules.md       # AI dev process rules (altijd geladen)
├── design/
│   └── styleguide.md          # Brand DNA, kleuren, typografie, anti-patterns
├── infra/
│   ├── deploy.md              # CI/CD, deploy verification, Alembic, Renovate
│   ├── servers.md             # Server inventory, SSH, iptables, DNS, recovery
│   └── sops-env.md            # SOPS, env vars, secrets management
├── lang/
│   ├── docker.md              # Docker Compose patterns en pitfalls
│   ├── python.md              # Python async, FastAPI, ruff, pyright
│   ├── testing.md             # Playwright, pytest, test patterns
│   └── typescript.md          # TypeScript, ESLint, TanStack, Tailwind
├── platform/
│   ├── caddy.md               # Caddy config, TLS, routing
│   ├── librechat.md           # LibreChat OIDC, MongoDB, Redis
│   ├── litellm.md             # LiteLLM tier aliases, model routing
│   ├── vllm.md                # vLLM GPU config, startup, NVIDIA MPS
│   └── zitadel.md             # Zitadel OIDC, PAT rotation, Login V2
├── projects/
│   ├── docs.md                # klai-docs (Next.js) patterns en pitfalls
│   ├── knowledge.md           # klai-knowledge-ingest, klai-connector, crawl4ai
│   ├── portal-backend.md      # Portal backend specifieke patterns
│   ├── portal-frontend.md     # Portal frontend tokens, forms, components
│   ├── portal-logging-py.md   # Python logging (structlog)
│   ├── portal-logging-ts.md   # TypeScript logging (tagged loggers)
│   ├── portal-security.md     # Multi-tenant IDOR, RLS, org_id scoping
│   ├── python-services.md     # Gedeelde Python service patterns
│   └── website.md             # klai-website design patterns
└── workflow/
    └── process-full.md        # Extended process pitfalls (geladen bij SPEC werk)
```

## Beslisregels: waar plaatsen?

### Stap 1: Is het platform-specifiek?
Gaat het over een specifiek platform component (Caddy, LiteLLM, LibreChat, vLLM, Zitadel)?
→ `platform/{component}.md`

### Stap 2: Is het infrastructuur?
Gaat het over servers, deployment, CI/CD, SOPS, env vars?
→ `infra/deploy.md` (CI/CD, deployments) of `infra/sops-env.md` (secrets) of `infra/servers.md` (server-specifiek)

### Stap 3: Is het taal/tool-specifiek?
Gaat het over Docker, Python patterns, TypeScript, testen in het algemeen?
→ `lang/{docker|python|typescript|testing}.md`

### Stap 4: Is het project-specifiek?
Gaat het over een specifiek Klai project (portal backend, portal frontend, website, docs)?
→ `projects/{portal-backend|portal-frontend|portal-security|portal-logging-py|portal-logging-ts|website|docs|knowledge|python-services}.md`

### Stap 5: Is het een universeel AI dev process regel?
Gaat het over hoe AI agents moeten debuggen, verifiëren, communiceren?
→ `pitfalls/process-rules.md` (compact, altijd geladen)
→ `workflow/process-full.md` (uitgebreid, geladen bij SPEC werk)

### Stap 6: Is het design/branding?
→ `design/styleguide.md`

## Hoe een entry toevoegen

### Binnen een bestaand bestand

Voeg gewoon een nieuwe `## sectie` toe op de logische plek in het bestand.
Er is geen apart index-bestand meer — de `paths:` frontmatter zorgt voor automatisch laden.

**Pattern** (herbruikbare oplossing):
```markdown
## Korte naam

**Wanneer:** [één zin: wanneer gebruik je dit]

[Uitleg waarom dit patroon bestaat]

```bash
# copy-paste ready commando's
```

**Regel:** [één-regel samenvatting]
```

**Pitfall** (fout + preventie):
```markdown
## Korte naam (CRIT|HIGH|MED)

[Wat er fout ging]

**Waarom:** [root cause]

**Preventie:** Specifieke actie die dit voorkomt.
```

### Nieuw bestand aanmaken

Alleen als het domein echt nergens past. Altijd met `paths:` frontmatter:

```markdown
---
paths:
  - "klai-{service}/**"
---
# {Service} Patterns
```

## Wat NIET hier staat

- **Git-gerelateerde regels** → die stonden in `pitfalls/git.md` (verwijderd, content was verouderd)
- **Code quality (ruff, ESLint)** → zit in `lang/python.md` en `lang/typescript.md`
- **Security pitfalls** → zit in `projects/portal-security.md`
- **Algemene index files** → er zijn geen `patterns.md` of `pitfalls.md` meer
