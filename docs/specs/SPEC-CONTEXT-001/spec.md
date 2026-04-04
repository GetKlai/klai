# SPEC-CONTEXT-001: Context Architecture Reorganisatie

## Doel

Het complete kennissysteem (`.claude/rules/klai/`, CLAUDE.md, hooks) reorganiseren zodat:
- De juiste kennis op het juiste moment laadt
- Geen duplicatie tussen bestanden
- Elk bestand max 50 regels (adherence daalt boven 200)
- Minimale context per sessie
- MoAI core (`.claude/rules/moai/`) wordt NIET aangepast

## Huidige situatie

### Token-impact per actie

| Actie | Bestanden die laden | Totaal regels | ~Tokens |
|---|---|---|---|
| Lees één `.py` bestand | 4 bestanden | 1.024 | ~10K |
| Lees één `.tsx` in portal | 7 bestanden | 1.610 | ~16K |
| Lees `docker-compose.yml` | 8 bestanden | 3.704 | ~37K |
| Always-loaded (elke sessie) | 5 bestanden | 291 | ~3K |

### Kernproblemen

1. **Bestanden zijn 5-22x te groot**: `pitfalls/platform.md` is 1.108 regels (doel: 50)
2. **Brede globs laden irrelevante content**: `**/*.py` laadt alle backend pitfalls ook bij een simpel script
3. **Dubbele organisatie**: `patterns/X.md` + `pitfalls/X.md` met dezelfde paths = dubbele load
4. **Monolithische platform files**: LiteLLM, Zitadel, Caddy in één bestand — maar je werkt nooit aan alle drie tegelijk
5. **Redundante indexen**: `knowledge.md`, `pitfalls.md`, `patterns.md` zijn wegwijzers die `paths:` al automatisch doet
6. **process.md vs process-rules.md**: dezelfde content in compact (135r, always) en uitgebreid (692r, conditional)

## Ontwerp: Vier lagen

### Laag 1 — Always loaded (max 50 regels)

Regels die ELKE sessie nodig zijn, ongeacht wat je doet.

**Huidige status:** 5 bestanden, 291 regels
**Doelstatus:** 1 bestand, max 50 regels

| Bestand | Actie | Reden |
|---|---|---|
| `process-rules.md` | **INKRIMPEN** naar 50 regels | Alleen de CRIT-regels behouden |
| `knowledge.md` | **VERWIJDEREN** | Redundant met `paths:` auto-loading |
| `pitfalls.md` | **VERWIJDEREN** | Redundant met `paths:` auto-loading |
| `patterns.md` | **VERWIJDEREN** | Redundant met `paths:` auto-loading |
| `serena.md` | **VERWIJDEREN** | Dupliceert CLAUDE.md Serena-sectie |

**Wat blijft in process-rules.md (max 50 regels):**
Alleen regels met severity CRIT die niet context-afhankelijk zijn:
- `listen-before-acting` — lees hele bericht voor actie
- `validate-before-code-change` — valideer hypothese met data
- `verify-completion-claims` — verifieer claims met git diff
- `evidence-only-confidence` — alleen bewijs telt als confidence
- `report-confidence` — eindig met Confidence: [0-100]
- `adversarial-at-high-confidence` — bug-hunt bij ≥80
- `diagnose-before-fixing` — stop, lees logs, één hypothese
- `minimal-changes` — alleen wat gevraagd is
- `wait-after-question` — stop na vraag
- `ask-before-retry` — na 2 pogingen, vraag user

Alle HIGH/MED regels verhuizen naar `pitfalls/process.md` (conditional, laadt bij SPEC-werk).

### Laag 2 — Per bestandstype (brede globs, max 50 regels per bestand)

Generieke regels die gelden voor ALLE bestanden van dat type, ongeacht project.

| Nieuw bestand | Paths | Inhoud (uit huidige bestanden) | Max regels |
|---|---|---|---|
| `lang/python.md` | `**/*.py` | asyncio gather, httpx timeout, ruff check, structlog basics, pyright | 50 |
| `lang/typescript.md` | `**/*.ts`, `**/*.tsx` | ESLint, code exploration (serena-first), component conventions | 50 |
| `lang/docker.md` | `**/Dockerfile`, `**/docker-compose*.yml` | Container preflight checks, rebuild patterns, image tags | 50 |
| `lang/testing.md` | `**/test_*.py`, `**/*_test.py`, `**/*.test.ts*` | Playwright patterns, test isolation, mock patterns | 50 |

**Wat komt NIET in Laag 2:**
- Project-specifieke patterns (multi-tenant → Laag 3)
- Platform-specifieke pitfalls (LiteLLM, Zitadel → Laag 3)
- Logging details (→ Laag 3, per project)
- Security/IDOR (→ Laag 3, portal-specifiek)

### Laag 3 — Per project/component (specifieke globs, max 50 regels per bestand)

Kennis die alleen relevant is als je in een specifiek project of component werkt.

#### Portal Backend
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `projects/portal-backend.md` | `klai-portal/backend/**/*.py` | FastAPI patterns, multi-tenant, API conventions, event emission | 50 |
| `projects/portal-security.md` | `klai-portal/backend/app/api/**/*.py` | IDOR, RLS, org-scope, ownership checks, 404-not-403 | 50 |
| `projects/portal-logging-py.md` | `klai-portal/backend/**/*.py` | structlog config, ProcessorFormatter, verboden patterns | 50 |

#### Portal Frontend
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `projects/portal-frontend.md` | `klai-portal/frontend/src/**` | Portal tokens, sidebar, forms, cards, Mantine 8 | 50 |
| `projects/portal-logging-ts.md` | `klai-portal/frontend/src/**` | consola + Sentry setup, geen console.log | 50 |

#### Styleguide (gedeeld portal + website)
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `design/styleguide.md` | `klai-portal/frontend/**`, `klai-website/**` | Brand DNA, kleuren, typografie, border radius, logo | 50 |

#### Website
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `projects/website.md` | `klai-website/**` | Website-specifieke patterns, buttons, animations | 50 |

#### Docs
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `projects/docs.md` | `klai-docs/**` | Next.js docs-app pitfalls, port, basepath, visibility | 50 |

#### Platform componenten (gesplitst uit platform.md 1.108 regels)
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `platform/litellm.md` | `**/litellm*.yml`, `deploy/litellm/**` | Tier model, provider prefix, drop_params, health endpoint | 50 |
| `platform/vllm.md` | `deploy/vllm/**`, `**/docker-compose*.yml` | GPU memory split, sequential startup, MPS enforce-eager | 50 |
| `platform/zitadel.md` | `deploy/zitadel/**` | Org-per-tenant, user grants, PAT rotation, login v2 | 50 |
| `platform/caddy.md` | `**/Caddyfile`, `deploy/caddy/**` | Tenant routing, CSP, basicauth monitoring, log directive | 50 |
| `platform/librechat.md` | `deploy/librechat/**` | OIDC tokens, Redis cache, addParams, dual system msg | 50 |

#### Infrastructure
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `infra/sops-env.md` | `**/.env*`, `**/*sops*`, `klai-infra/**` | SOPS sync, env safety, dollar signs, placeholder vals | 50 |
| `infra/deploy.md` | `deploy/**`, `.github/**/*.yml` | CI verification, post-push health, deploy workflow | 50 |
| `infra/servers.md` | `klai-infra/**` | SSH access, server layout, Docker networking | 50 |

#### Python microservices (momenteel niet gedekt)
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `projects/python-services.md` | `klai-scribe/**`, `klai-focus/**`, `klai-connector/**`, `klai-knowledge-*/**`, `klai-retrieval-api/**`, `klai-mailer/**` | Gedeelde patterns voor microservices: logging setup, health endpoints, Alembic | 50 |

#### Process (conditional)
| Nieuw bestand | Paths | Inhoud | Max |
|---|---|---|---|
| `workflow/process-full.md` | `**/.workflow/specs/**`, `**/docs/specs/**` | Alle HIGH/MED procesregels (uitgebreid formaat) | Ongelimiteerd (laadt alleen bij SPEC-werk) |

### Laag 4 — Per activiteit (hooks)

Hooks blijven voor mechanische acties, niet voor kennis.

| Hook | Event | Actie | Status |
|---|---|---|---|
| `git-safety-guard.sh` | PreToolUse:Bash | Blokkeert destructieve git (exit 2) | ✅ Bestaat |
| `domain-context-injection.sh` | PreToolUse:Bash | Print reminders bij ssh/docker/sops/alembic/curl | ✅ Bestaat |
| `portal-api-preflight.sh` | PreToolUse:Bash | Portal API deploy checks | ✅ Bestaat |
| `playwright-url-guard.sh` | PreToolUse:navigate | URL validatie | ✅ Bestaat |
| `post-push-ci.py` | PostToolUse:Bash | CI monitoring na push | ✅ Bestaat |

Geen nieuwe hooks nodig — de kennis gaat naar `paths:` bestanden.

## Migratie-mapping: oud → nieuw

### Bestanden die VERWIJDERD worden

| Oud bestand | Regels | Reden |
|---|---|---|
| `knowledge.md` | 56 | Always-loaded index, redundant met paths: |
| `pitfalls.md` | 62 | Always-loaded index, redundant met paths: |
| `patterns.md` | 38 | Always-loaded index, redundant met paths: |
| `serena.md` | 80 | Dupliceert CLAUDE.md |

### Bestanden die OPGESPLITST worden

| Oud bestand | Regels | Wordt → |
|---|---|---|
| `pitfalls/platform.md` | 1.108 | `platform/litellm.md`, `platform/vllm.md`, `platform/zitadel.md`, `platform/caddy.md`, `platform/librechat.md` |
| `patterns/platform.md` | 695 | Merged in bovenstaande platform/* bestanden |
| `patterns/frontend.md` | 814 | `lang/typescript.md` (generiek) + `projects/portal-frontend.md` (specifiek) |
| `pitfalls/infrastructure.md` | 535 | `infra/sops-env.md` + `infra/deploy.md` + `infra/servers.md` |
| `patterns/devops.md` | 448 | `infra/deploy.md` + `lang/docker.md` |
| `pitfalls/backend.md` | 446 | `lang/python.md` (generiek) + `projects/portal-backend.md` (specifiek) |
| `pitfalls/devops.md` | 419 | `infra/deploy.md` + `lang/docker.md` |
| `patterns/infrastructure.md` | 347 | `infra/sops-env.md` + `infra/servers.md` |
| `pitfalls/security.md` | 312 | `projects/portal-security.md` |
| `patterns/backend.md` | 258 | `lang/python.md` + `projects/portal-backend.md` |
| `patterns/code-quality.md` | 227 | `lang/python.md` + `lang/typescript.md` (generiek per taal) |
| `pitfalls/code-quality.md` | 93 | Merged in `lang/python.md` + `lang/typescript.md` |

### Bestanden die INGEKROMPEN worden

| Oud bestand | Regels | Wordt → | Doel |
|---|---|---|---|
| `process-rules.md` | 135 | `process-rules.md` | 50 |
| `styleguide.md` | 154 | `design/styleguide.md` | 50 |
| `portal-patterns.md` | 122 | `projects/portal-frontend.md` | 50 |
| `website-patterns.md` | 145 | `projects/website.md` | 50 |
| `pitfalls/docs-app.md` | 198 | `projects/docs.md` | 50 |
| `python-logging.md` | 87 | `projects/portal-logging-py.md` | 50 |
| `logging.md` | 74 | `projects/portal-logging-ts.md` | 50 |
| `patterns/logging.md` | 188 | Merged in logging-bestanden per project | — |
| `post-push.md` | 103 | `infra/deploy.md` | 50 |

### Bestanden die ONGEWIJZIGD blijven

| Bestand | Regels | Reden |
|---|---|---|
| `confidence.md` | 39 | Al klein, specifieke paths |
| `server-secrets.md` | 21 | Al klein, specifieke paths |
| `multi-tenant-pattern.md` | 67 | Wordt → `projects/portal-backend.md` (merge) |
| `container-preflight.md` | 49 | Wordt → `lang/docker.md` (merge) |

## Resultaat na reorganisatie

### Token-impact per actie (nieuw)

| Actie | Bestanden die laden | Totaal regels | ~Tokens | Reductie |
|---|---|---|---|---|
| Lees één `.py` (scribe) | `lang/python.md` + `projects/python-services.md` | ~100 | ~1K | **-90%** |
| Lees `.py` (portal backend) | `lang/python.md` + `projects/portal-backend.md` + `portal-security.md` + `portal-logging-py.md` | ~200 | ~2K | **-80%** |
| Lees `.tsx` (portal) | `lang/typescript.md` + `projects/portal-frontend.md` + `design/styleguide.md` + `portal-logging-ts.md` | ~200 | ~2K | **-88%** |
| Lees `docker-compose.yml` | `lang/docker.md` + `infra/deploy.md` | ~100 | ~1K | **-97%** |
| Lees `litellm.yml` | `lang/docker.md` + `platform/litellm.md` | ~100 | ~1K | **-97%** |
| Always-loaded | `process-rules.md` | ~50 | ~500 | **-83%** |

### Bestandsstructuur (nieuw)

```
.claude/rules/klai/
├── process-rules.md              # Laag 1: always (max 50r)
├── confidence.md                 # paths: confidence-check.py (39r)
├── server-secrets.md             # paths: .env, sops (21r)
├── lang/                         # Laag 2: per bestandstype
│   ├── python.md                 # paths: **/*.py (max 50r)
│   ├── typescript.md             # paths: **/*.ts(x) (max 50r)
│   ├── docker.md                 # paths: Dockerfile, docker-compose (max 50r)
│   └── testing.md                # paths: test files (max 50r)
├── projects/                     # Laag 3: per project
│   ├── portal-backend.md         # paths: klai-portal/backend/** (max 50r)
│   ├── portal-security.md        # paths: klai-portal/backend/app/api/** (max 50r)
│   ├── portal-frontend.md        # paths: klai-portal/frontend/src/** (max 50r)
│   ├── portal-logging-py.md      # paths: klai-portal/backend/** (max 50r)
│   ├── portal-logging-ts.md      # paths: klai-portal/frontend/src/** (max 50r)
│   ├── website.md                # paths: klai-website/** (max 50r)
│   ├── docs.md                   # paths: klai-docs/** (max 50r)
│   └── python-services.md        # paths: klai-scribe/**, klai-focus/**, etc. (max 50r)
├── platform/                     # Laag 3: per platform component
│   ├── litellm.md                # paths: litellm*.yml (max 50r)
│   ├── vllm.md                   # paths: deploy/vllm/** (max 50r)
│   ├── zitadel.md                # paths: deploy/zitadel/** (max 50r)
│   ├── caddy.md                  # paths: Caddyfile, deploy/caddy/** (max 50r)
│   └── librechat.md              # paths: deploy/librechat/** (max 50r)
├── infra/                        # Laag 3: infrastructure
│   ├── sops-env.md               # paths: .env*, sops*, klai-infra/** (max 50r)
│   ├── deploy.md                 # paths: deploy/**, .github/** (max 50r)
│   └── servers.md                # paths: klai-infra/** (max 50r)
├── design/                       # Laag 3: design
│   └── styleguide.md             # paths: portal/frontend/**, klai-website/** (max 50r)
└── workflow/                     # Laag 3: workflow (groter toegestaan)
    └── process-full.md           # paths: specs dirs (uitgebreide procesregels)
```

## Implementatieplan

### Fase 1: Voorbereiding
1. Maak de nieuwe directorystructuur aan (`lang/`, `projects/`, `platform/`, `infra/`, `design/`, `workflow/`)
2. Maak een backup-branch

### Fase 2: Laag 1 — Always loaded inkrimpen
1. Trim `process-rules.md` naar max 50 regels (alleen CRIT)
2. Verwijder `knowledge.md`, `pitfalls.md`, `patterns.md`, `serena.md`

### Fase 3: Laag 2 — Per bestandstype
1. Maak `lang/python.md` — extracteer generieke Python regels uit `patterns/backend.md`, `pitfalls/backend.md`, `patterns/code-quality.md`
2. Maak `lang/typescript.md` — extracteer uit `patterns/frontend.md`, `pitfalls/frontend.md`, `patterns/code-quality.md`
3. Maak `lang/docker.md` — extracteer uit `container-preflight.md`, `patterns/devops.md`
4. Maak `lang/testing.md` — extracteer uit `patterns/testing.md`

### Fase 4: Laag 3 — Per project
1. Maak `projects/portal-backend.md` — uit `patterns/backend.md`, `multi-tenant-pattern.md`
2. Maak `projects/portal-security.md` — uit `pitfalls/security.md`
3. Maak `projects/portal-frontend.md` — uit `portal-patterns.md`, `patterns/frontend.md`
4. Maak `projects/portal-logging-py.md` — uit `python-logging.md`, `patterns/logging.md`
5. Maak `projects/portal-logging-ts.md` — uit `logging.md`
6. Maak `design/styleguide.md` — uit `styleguide.md` (trim naar 50)
7. Maak `projects/website.md` — uit `website-patterns.md`
8. Maak `projects/docs.md` — uit `pitfalls/docs-app.md`
9. Maak `projects/python-services.md` — nieuw, voor ongedekte services
10. Split `pitfalls/platform.md` + `patterns/platform.md` in 5 platform-bestanden
11. Split infra-bestanden in 3 infra-bestanden

### Fase 5: Workflow
1. Maak `workflow/process-full.md` — uit `pitfalls/process.md` (conditional)

### Fase 6: Opruimen
1. Verwijder alle oude bestanden die vervangen zijn
2. Verifieer dat alle regels gemigreerd zijn (diff check)
3. Test: lees een `.py` bestand, check welke rules laden
4. Update CLAUDE.md referenties indien nodig

## Beperkingen (MoAI core)

MoAI (`.claude/rules/moai/`) wordt NIET aangepast. Dit betekent:
- MoAI's eigen file-reading-optimization.md en coding-standards.md blijven ongewijzigd
- Als MoAI regels heeft die overlappen met onze klai regels, accepteren we die overlap
- MoAI's agent-hooks.md en skill-authoring.md zijn niet relevant voor deze reorganisatie

## Risico's

1. **Informatie-verlies bij trimming**: Bij het inkrimpen van 1108 naar 5×50 regels gaat detail verloren. Mitigatie: alleen de kern-regel behouden, niet het uitgebreide verhaal.
2. **Glob-specificiteit**: Sommige platform-bestanden (vllm.md) triggeren alleen op `deploy/vllm/**` — als vLLM config ergens anders staat, mist het. Mitigatie: controleer bestaande bestandslocaties.
3. **Vexa-pitfalls**: `pitfalls/vexa-leave-detection.md` (202 regels) is project-specifiek voor een feature die mogelijk deprecated is. Actie: beoordeel of dit nog nodig is.

## Acceptatiecriteria

- [ ] Geen enkel bestand in `.claude/rules/klai/` overschrijdt 50 regels (behalve `workflow/process-full.md`)
- [ ] Always-loaded bestanden: maximaal 1 bestand, max 50 regels
- [ ] Elke regel uit de oude bestanden is traceerbaar gemigreerd naar een nieuw bestand
- [ ] Token-impact per actie is ≥70% lager dan huidig
- [ ] Geen dubbele content tussen bestanden
- [ ] Alle `paths:` globs matchen daadwerkelijk bestaande bestandslocaties
- [ ] MoAI core is ongewijzigd
