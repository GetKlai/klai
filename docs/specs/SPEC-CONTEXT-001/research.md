# SPEC-CONTEXT-001: Context Architecture Reorganisatie — Research

## Doel

Het complete kennissysteem (.claude/rules/, CLAUDE.md, hooks) reorganiseren zodat:
- De juiste kennis op het juiste moment laadt
- Geen duplicatie
- Minimale context per sessie
- MoAI core (`.claude/rules/moai/`) wordt NIET aangepast

## 1. Beschikbare mechanismen

### 1a. `paths:` frontmatter in `.claude/rules/*.md`
- **Trigger**: wanneer Claude een bestand LEEST (Read tool) dat matcht met het glob pattern
- **Write triggert NIET**: nieuw bestand aanmaken zonder eerst te lezen laadt regels niet
- **Eenmalig**: eenmaal geladen → blijft actief de hele sessie
- **Subagents**: laden dezelfde paths-regels als parent
- **Betrouwbaarheid**: ~95% — mechanisch, niet interpretatief
- **Specificiteit**: globs kunnen zo breed (`**/*.py`) of smal (`klai-scribe/app/**/*.py`) als nodig

### 1b. `.claude/rules/*.md` ZONDER `paths:`
- **Trigger**: altijd, elke sessie, onvoorwaardelijk
- **Subagents**: ook geladen
- **Kosten**: permanent context verbruik — elke regel kost tokens in ELKE sessie

### 1c. CLAUDE.md bestanden
- **Root**: altijd geladen bij sessiestart (~95% betrouwbaar)
- **Subdirectory**: geladen wanneer Claude een bestand leest in die directory (~70-80% betrouwbaar)
- **Subagents**: laden root CLAUDE.md; subdirectory CLAUDE.md alleen als ze daar bestanden lezen
- **Besluit**: subdirectory CLAUDE.md ALLEEN voor echte eigen repo's (klai-portal, klai-website, klai-infra)

### 1d. PreToolUse hooks (stdout)
- **Trigger**: vóór elke tool-aanroep die matcht met het hook-filter
- **Stdout**: wordt als system message getoond aan Claude — LAADT GEEN bestanden
- **Exit 2**: blokkeert de tool-aanroep
- **Subagents**: hooks draaien ook voor subagent tool calls
- **Betrouwbaarheid**: 100% mechanisch

### 1e. SubagentStart hooks
- **Trigger**: wanneer een subagent spawnt
- **Kan**: `additionalContext` injecteren → wordt onderdeel van subagent context
- **Krachtig**: domeinkennis meegeven aan subagents op basis van type/naam

### 1f. UserPromptSubmit hooks
- **Trigger**: wanneer gebruiker een prompt indient
- **Kan**: `additionalContext` injecteren + blokkeren (exit 2)
- **Krachtig**: sessie-brede context op basis van wat de gebruiker vraagt

### 1g. Skills (progressive disclosure)
- **Level 1**: metadata altijd geladen (~100 tokens)
- **Level 2**: body geladen bij trigger match (~5000 tokens)
- **Level 3**: bundled files on-demand
- **MoAI-specifiek**: niet aanpassen

### 1h. Serena voor subagents
- Project-level subagents (`.claude/agents/`) kunnen Serena tools gebruiken:
  - `tools` veld weglaten → erft alles inclusief MCP tools (meest betrouwbaar)
  - `mcpServers: [serena]` in frontmatter → deelt bestaande connectie
- Plugin subagents kunnen het NIET (hard beperking)
- Bekende bugs: hallucineren van MCP-resultaten bij complexe prompts

## 2. Huidige inventaris

### 2a. Always-loaded bestanden (5 bestanden, ~290 regels)

| Bestand | Regels | Inhoud | Probleem |
|---|---|---|---|
| `pitfalls/process-rules.md` | ~135 | Compacte procesregels | Veel overlap met process.md |
| `knowledge.md` | ~56 | Index van domeinbestanden | Passieve verwijzingen, niet mechanisch |
| `pitfalls.md` | ~62 | Index van pitfall-bestanden | Passieve verwijzingen, niet mechanisch |
| `patterns.md` | ~38 | Index van pattern-bestanden | Passieve verwijzingen, niet mechanisch |
| `serena.md` | ? | Serena gebruiksrichtlijnen | Deels naar CLAUDE.md verhuisd |

### 2b. Conditionele bestanden — per bestandstype (breed)

| Glob | Bestanden die laden | Probleem |
|---|---|---|
| `**/*.py` | backend patterns, backend pitfalls, code-quality (2x) | Laadt FastAPI pitfalls ook bij simpele scripts |
| `**/*.ts(x)` | frontend patterns, frontend pitfalls, code-quality (2x) | Laadt ook bij klai-docs TypeScript |
| `**/*.sh` | devops patterns, devops pitfalls | Elk shell script laadt devops context |
| `**/docker-compose*.yml` | 6+ bestanden | Enorme overlap |
| `**/*.py` + `**/pyproject.toml` | backend + code-quality | Dubbele triggers |

### 2c. Conditionele bestanden — per project (specifiek)

| Project | Eigen context? | Via |
|---|---|---|
| klai-portal/backend | Goed gedekt | CLAUDE.md + multi-tenant + python-logging + security |
| klai-portal/frontend | Goed gedekt | CLAUDE.md + portal-patterns + styleguide + logging |
| klai-docs | Deels | pitfalls/docs-app.md |
| klai-website | Goed gedekt | 2x CLAUDE.md + website-patterns + styleguide |
| klai-infra | Goed gedekt | CLAUDE.md + infra patterns/pitfalls + server-secrets |
| klai-scribe | NIET gedekt | Alleen generiek Python |
| klai-focus | NIET gedekt | Alleen generiek Python |
| klai-connector | NIET gedekt | Alleen generiek Python |
| klai-knowledge-ingest | Deels | crawl4ai pitfall + generiek Python |
| klai-knowledge-mcp | NIET gedekt | Alleen generiek Python |
| klai-retrieval-api | NIET gedekt | Alleen generiek Python |
| klai-mailer | NIET gedekt | Alleen generiek Python |

### 2d. Hooks

| Hook | Event | Actie |
|---|---|---|
| domain-context-injection.sh | PreToolUse:Bash | Print context bij ssh/docker/sops/alembic/curl |
| git-safety-guard.sh | PreToolUse:Bash | Blokkeert destructieve git |
| portal-api-preflight.sh | PreToolUse:Bash | Portal API deploy checks |
| playwright-url-guard.sh | PreToolUse:navigate | URL validatie |
| post-push-ci.py | PostToolUse:Bash | CI monitoring na push |
| playwright-browser-cleanup.sh | Stop | Browser opruimen |
| confidence-check.py | Stop | Confidence afdwingen |

### 2e. Dubbele logging bestanden

`python-logging.md` en `patterns/logging.md` hebben bijna identieke paths:
- python-logging.md: 7 subprojecten (portal, connector, knowledge-mcp, mailer, retrieval-api, scribe, focus)
- patterns/logging.md: 8 subprojecten + logging_setup.py + alloy + docker-compose

## 3. Trigger-categorieën

### A. Per subproject
"Ik werk aan klai-scribe" → architectuur, patterns, pitfalls, deploy-specifics van scribe.
**Best mechanism**: `paths:` met specifieke globs (`klai-scribe/**/*.py`).
**Niet**: subdirectory CLAUDE.md (alleen voor echte eigen repo's).

### B. Per bestandstype
"Ik lees een .py bestand" → Python patterns, pitfalls, code-quality, logging.
**Best mechanism**: `paths:` maar met HIËRARCHIE om redundantie te voorkomen.
**Probleem**: `**/*.py` is te breed. Moet opgesplitst in generiek Python vs project-specifiek.

### C. Per activiteit/actie
"Ik ga deployen" → devops patterns, post-push, CI workflow.
**Best mechanism**: combinatie van `paths:` (voor bestandstriggers) + hooks (voor commandotriggers).

### D. Altijd nodig (sessie-breed)
Procesregels die bij ELKE taak gelden.
**Best mechanism**: always-loaded rules, maar minimaal gehouden.

## 4. Kernprobleem: paths-hiërarchie

Het grote designprobleem is: hoe voorkom je redundantie tussen:
- **Generieke** regels (`**/*.py` → alle Python)
- **Project-specifieke** regels (`klai-scribe/**/*.py` → alleen scribe)

Als beide triggeren bij het lezen van `klai-scribe/app/main.py`, laadt je dubbele content.

### Opties:
1. **Generiek + specifiek naast elkaar**: accepteer overlap, houd bestanden klein
2. **Alleen specifiek**: geen `**/*.py` meer, elk project apart → veel duplicatie in paths
3. **Gelaagd**: generiek bestand bevat alleen wat ALTIJD geldt voor dat bestandstype, project-specifiek vult aan
4. **Conditioneel in hook**: UserPromptSubmit hook detecteert project en injecteert de juiste context

## 5. Aanvullend onderzoek — paths-hiërarchie en token-impact

### 5a. Token-rekensom

| Scenario | Regels | Tokens | Oordeel |
|---|---|---|---|
| 5 kleine files (30 regels/stuk) tegelijk | 150 | ~1.000 | Verwaarloosbaar |
| 10 kleine files tegelijk | 300 | ~2.000 | Acceptabel |
| 5 grote files (200+ regels/stuk) tegelijk | 1.000+ | ~6.000+ | Problematisch — adherence daalt |
| ALLE 66 files (als paths: bug actief) | 11.600 | ~58.000-93.000 | Kritiek op 200K window |

**Kernregel van Anthropic**: "Longer files consume more context and reduce adherence." Boven 200 regels per file daalt compliance meetbaar.

### 5b. Kritieke bug: laden paths: bestanden altijd? (#16299)

GitHub issue #16299 rapporteert dat rules MET `paths:` mogelijk toch bij sessie-start laden, ongeacht of er een match is. Als dit actief is in onze versie, laden ALLE 66 bestanden bij elke sessie.

**ACTIE NODIG**: test dit. Gebruik een `InstructionsLoaded` hook of voeg een uniek marker toe aan een path-scoped file en check of het in context zit bij een niet-matchende sessie.

### 5c. Hoe andere tools het oplossen

| Tool | Aanpak | Monorepo-support |
|---|---|---|
| Cursor | `globs:` + `alwaysApply: true/false` toggle | Nested rules directories per subproject |
| Windsurf | "Context-Specific" rule type + RAG | Directory-scoped AGENTS.md |
| Cline | Geen path-scoping | Transparante context-weergave |
| Aider | Geen path-scoping | Git-centric |

Cursor is het verst met monorepo-support. Hun patroon: root rules voor architectuur-overzicht, per-app nested rules voor specifieke context.

### 5d. Community-consensus: "small files" lost overlap op

Als elke rules file **max 30-50 regels** is, wordt overlap tussen generiek (`**/*.py`) en specifiek (`klai-scribe/**/*.py`) irrelevant. De token-cost van 5-10 overlappende kleine files (~1.000-2.000 tokens) is verwaarloosbaar vergeleken met de 200K+ context window.

**Dit is de aanbevolen strategie**: maak files KLEIN, accepteer overlap, focus op adherence (compliance).

### 5e. Tool: claude-rules-doctor

De tool `claude-rules-doctor` (github.com/nulone/claude-rules-doctor) kan verifiëren welke `paths:` globs daadwerkelijk matchen. Kan helpen bij het testen van onze glob-configuratie.

## 6. Ontwerpbeslissingen (op basis van onderzoek)

### Beslissing 1: File-grootte limiet
**Max 50 regels per rules file.** Boven 200 regels daalt adherence. Onze huidige files (sommige 800-1100 regels) zijn veel te groot.

### Beslissing 2: Overlap is acceptabel bij kleine files
Generiek (`**/*.py`) + specifiek (`klai-scribe/**/*.py`) mogen BEIDE laden. Bij max 50 regels per file is de overlap ~1.000 tokens — verwaarloosbaar.

### Beslissing 3: Gelaagde structuur
- **Laag 1 — Altijd geladen** (process-rules): max 50 regels, alleen regels die ECHT elke sessie nodig zijn
- **Laag 2 — Per bestandstype** (`**/*.py`, `**/*.ts`): generieke taal/framework regels, max 50 regels
- **Laag 3 — Per project** (`klai-scribe/**`): project-specifieke architectuur, deploy, pitfalls, max 50 regels
- **Laag 4 — Per activiteit** (hooks): mechanische enforcement voor commando's (ssh, docker, sops, git)

### Beslissing 4: Subdirectory CLAUDE.md alleen voor eigen repo's
klai-portal, klai-website, klai-infra hebben eigen CLAUDE.md (zijn aparte repo's).
Alle andere subprojecten krijgen `paths:`-bestanden in `.claude/rules/klai/`.

### Beslissing 5: Hooks blijven voor acties, niet voor kennis
Hooks zijn 100% betrouwbaar voor blokkeren en reminders. Ze laden geen bestanden.
Voor kennisinjection: gebruik `paths:` of `UserPromptSubmit` met `additionalContext`.

### Beslissing 6: Test paths: bug eerst
Voordat we reorganiseren, moeten we verifiëren of `paths:` conditioneel werkt in onze versie. Als alles altijd laadt (bug #16299), is de hele paths-strategie zinloos en moeten we naar skills of hooks met additionalContext.
