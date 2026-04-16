---
description: >
  Volledig autonome pipeline voor een goedgekeurde SPEC: run → interview-standard review
  (auto-fix) → migraties → sync → e2e → merge. Geen menselijke stops — alleen de SPEC
  annotation (plan fase) vereist goedkeuring. Gebruik --review voor handmatige gates.
argument-hint: "SPEC-XXX [--no-e2e] [--no-merge] [--no-issue] [--review]"
---

# /klai:auto — Klai Autonomous Pipeline

**Input**: $ARGUMENTS (SPEC-ID verplicht, bijv. `SPEC-KB-003`)

Pre-execution git context:
!`git branch --show-current 2>/dev/null`
!`git status --porcelain 2>/dev/null | head -10`

---

## Doel

Voer de complete post-SPEC cyclus volledig autonoom uit. Geen menselijke goedkeuring
vereist. De enige bewuste stop in de totale workflow is de SPEC annotation in `/moai plan`
— die heeft de gebruiker al gedaan.

**Flags:**
- `--no-e2e`: Sla E2E tests over (bijv. als dev server niet draait)
- `--no-merge`: Stop na sync + E2E, merge niet automatisch
- `--no-issue`: Geen GitHub Issue aanmaken
- `--review`: Voeg twee menselijke goedkeuringsstops toe (na interview review + voor merge)

**Volledige flow (geen --review):**
```
run → interview-standard auto-fix → migraties → sync --pr → e2e → merge
```

**Met --review:**
```
run → [STOP: interview review] → migraties → sync --pr → e2e → [STOP: finale gate] → merge
```

---

## Phase 0: SPEC Verificatie

Lees `.moai/specs/{SPEC-ID}/spec.md` om te bevestigen dat de SPEC bestaat.

Als de SPEC niet bestaat: geef een foutmelding en stop.

Bepaal:
- `spec_title`: de titel van de SPEC
- `branch_name`: `feature/{SPEC-ID}` (lees uit git-strategy.yaml)
- `review_mode`: true als `--review` vlag aanwezig
- `has_e2e`: true tenzij `--no-e2e` vlag aanwezig

---

## Phase 1: Implementatie

Roep de run workflow aan via `Skill("moai:run")` met het SPEC-ID als argument en
`--solo` mode. Wacht op de completion marker `<moai>DONE</moai>` of `<moai>COMPLETE</moai>`.

Als run de completion marker geeft maar er zijn test failures of build errors:
- Delegeer aan `expert-debug` subagent met de exacte foutmelding
- Verifieer dat de fix de tests laat slagen
- Herhaal tot alle tests groen zijn (max 10 iteraties)
- Na 10 iteraties zonder succes: rapporteer wat er nog openstaat en stop

Dit is hetzelfde gedrag als tijdens normale coding — build breekt, fix, herhaal.

---

## Phase 2: Interview Standard Review & Auto-Fix

Voer een grondige kwaliteitsreview uit met een hogere lat dan standaard TRUST 5.
De vraag: **zou een senior developer trots zijn op deze code?**

### Phase 2.1: Interview Standard Review

Delegeer aan `manager-quality` subagent:

> "Voer een 'interview standard' review uit van alle gewijzigde bestanden op
> deze branch (`git diff main...HEAD`). De vraag is: zou een senior developer
> trots zijn op deze code? Zou je het tonen in een technisch interview als je
> beste werk?
>
> Review vanuit vier perspectieven:
>
> **1. Onderhoudbaarheid**
> - Zijn namen beschrijvend en consistent?
> - Geen magic numbers of hardcoded strings zonder constanten
> - Geen TODO/FIXME/HACK comments in gecommitte code
> - Zijn functies kort genoeg (max ~30 regels)?
> - Single Responsibility Principle gevolgd?
>
> **2. Architectuur & patronen**
> - Volgt de code de bestaande patronen in `.moai/project/structure.md`?
> - Geen onnodige abstracties (YAGNI)?
> - Geen duplicatie (DRY)?
> - Dependency injection waar van toepassing?
>
> **3. Robuustheid**
> - Correcte foutafhandeling (geen bare `except`, geen stille failures)?
> - Edge cases gedekt in tests?
> - N+1 query risico's?
> - Geen resource leaks (open files, connections)?
>
> **4. Leesbaarheid**
> - Zijn complexe stukken uitgelegd via code (niet alleen comments)?
> - Is de flow logisch te volgen zonder documentatie?
>
> Geef per bevinding: bestand, regelnummer, ernst (CRITICAL/IMPORTANT/SUGGESTION),
> en een concrete verbetersuggestie."

### Phase 2.2: Auto-fix alle bevindingen

Voor elke CRITICAL en IMPORTANT bevinding:
- Delegeer aan `expert-refactoring` subagent met de specifieke bevinding
- Verifieer dat tests nog slagen na de fix
- Maximum 3 auto-fix rondes per bevinding
- Als een bevinding na 3 rondes niet opgelost is: log als "unresolved" en ga door

SUGGESTION bevindingen: log in rapport maar fix niet automatisch.

### Phase 2.3: Review rapport (geen stop, tenzij --review)

Stel het interview standard rapport samen:
- CRITICAL gevonden: N (auto-fixed: M, unresolved: K)
- IMPORTANT gevonden: N (auto-fixed: M, unresolved: K)
- SUGGESTIONS: lijst (niet gefixed)

**Als `--review` flag aanwezig:**
```
AskUserQuestion:
  "Interview Standard Review: [samenvatting].
   Doorgaan naar sync en push naar main?"
  Opties:
  - Doorgaan → phase 3
  - Nog meer refactoren → geef instructie, ga terug naar 2.1
  - Stoppen → bewaar branch, stop pipeline
```

**Zonder `--review` flag:** log het rapport en ga direct door naar Phase 3.
Unresolved issues worden opgenomen in de PR description zodat ze zichtbaar zijn.

---

## Phase 3: Migratie Check & Run

**Achtergrond:** Alembic migraties worden bijna nooit gedraaid in de workflow
terwijl ze wel aangemaakt worden. Dit lost dat op door ze autonoom op productie
te draaien via SSH naar core-01.

**Kritieke regels (uit deploy.md):**
- Container naam: `klai-core-portal-api-1` (NIET `portal-api`)
- SSH: `ssh core-01` — NOOIT direct IP
- Alembic heeft DDL-rechten nodig; als het faalt door `must be owner`, draai de SQL
  direct via: `docker exec klai-core-postgres-1 psql -U klai -d klai -c "<SQL>"`

### Phase 3.1: Detecteer nieuwe migraties

```bash
git diff main...HEAD --name-only -- "klai-portal/backend/alembic/versions/*.py"
```

Als geen migraties gevonden: sla Phase 3.2 t/m 3.5 over en ga door naar Phase 4.

### Phase 3.2: Wacht op CI deploy

Na merge naar main deployt CI automatisch de nieuwe Docker image naar core-01.
Wacht tot de portal-api workflow groen is:

```bash
gh run watch --exit-status
```

Verifieer dat de nieuwe container draait (CreatedAt moet recent zijn):
```bash
ssh core-01 "docker ps --filter name=portal-api --format 'table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}'"
```

### Phase 3.3: Valideer migraties op productie

Check op conflicterende heads:
```bash
ssh core-01 "docker exec klai-core-portal-api-1 alembic heads 2>&1"
```

Als meerdere heads zonder duidelijke merge-migratie: maak een merge-migratie aan,
commit en push, wacht op CI, ga daarna verder.

Controleer de huidige DB state:
```bash
ssh core-01 "docker exec klai-core-portal-api-1 alembic current 2>&1"
```

### Phase 3.4: Draai migraties op productie via SSH

```bash
ssh core-01 "docker exec klai-core-portal-api-1 alembic upgrade head 2>&1"
```

Verifieer succes: exit code 0 en de nieuwe revision verschijnt in `alembic current`.

**Als migratie faalt met `must be owner of table`:**
De portal-api user heeft geen DDL rechten. Draai de SQL direct als klai superuser:
```bash
ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai -c '<SQL uit migratie>'"
```
Dan stamp de migratie als applied:
```bash
ssh core-01 "docker exec klai-core-portal-api-1 alembic stamp <revision_id> 2>&1"
```

**Als migratie faalt met `constraint/index does not exist` (constraint al verwijderd):**
De constraint is al weg in productie. Stamp de migratie als done en draai alleen
de ontbrekende stappen handmatig via psql als klai user.

**Als een nieuwe env var nodig is voor de SPEC (bijv. een JWT secret):**
1. Genereer: `python3 -c "import secrets; print(secrets.token_hex(32))"`
2. Voeg toe via SOPS-procedure op core-01 (zie sops-env.md)
3. Push klai-infra → wacht op "Sync .env to core-01" workflow
4. Herstart de container: `ssh core-01 "cd /opt/klai && docker compose up -d portal-api"`

**Als na alle pogingen de migratie niet lukt:**
Log als "MIGRATION FAILED — handmatige actie vereist" en ga door naar Phase 4.
Pipeline stopt niet — noteer de exacte foutmelding in het eindrapport.

### Phase 3.5: Migratie samenvatting

Log: "N nieuwe migratie(s) [succesvol gedraaid op core-01 / gefaald — zie rapport]."

---

## Phase 4: Sync & PR

Delegeer aan sync workflow via `Skill("moai:sync")` met `--pr` vlag
(en `--no-issue` als die vlag meegegeven is).

Wacht op:
- PR URL in output
- Sync completion marker

Sla de PR URL op voor gebruik in Phase 5 en 6.

Onresolved interview issues en migratie-status worden automatisch opgenomen
in de PR description door de sync workflow.

---

## Phase 5: E2E Tests

Sla over als `--no-e2e` vlag aanwezig is.

**Test altijd tegen productie** (`https://my.getklai.com`) — niet lokaal.
De echte deployment is wat telt; lokale tests missen integratieproblemen met de
echte database, SOPS-secrets en productie-configuratie.

### Phase 5.1: Wacht op productie-rollout

Verifieer dat portal-api en portal-frontend recent deployed zijn:
```bash
ssh core-01 "docker ps --filter name=portal-api --format '{{.Names}}\t{{.CreatedAt}}'"
ssh core-01 "ls -lt /srv/klai-portal/assets/*.js | head -3"
```

### Phase 5.2: Draai E2E via Playwright MCP

Gebruik de Playwright MCP tools direct (browser_navigate, browser_snapshot,
browser_click) om de acceptance criteria uit `spec.md` te testen op `https://my.getklai.com`.

Volg de Playwright workflow uit `.claude/rules/klai/lang/testing.md`:
1. `pkill -x "Brave Browser"` om vorige sessie te cleanen
2. `browser_navigate({ url: 'https://my.getklai.com' })`
3. Gebruik `browser_snapshot()` voor assertions (niet screenshots)
4. Interact via `ref` vanuit de snapshot
5. Sluit browser na afloop: alle tabs sluiten dan `browser_close()`

Focus op de user journeys die door de SPEC geïmplementeerd zijn.
Lees de acceptance criteria uit `spec.md` om te bepalen welke flows te testen.

### Phase 5.3: API endpoint verificatie

Voor backend-only endpoints: test direct met curl:
```bash
curl -s https://my.getklai.com/{endpoint} -H "Origin: https://example.com"
```

Verwachte responses: 401 voor auth-required, 404 voor niet-bestaande resources,
200 met juiste body voor publieke endpoints.

Als E2E tests falen:
- **Autonoom:** probeer auto-fix via `expert-debug` (max 2 pogingen)
- Fix → push → wacht op CI → hertest op productie
- Als na 2 pogingen nog gefaald: log als "E2E FAILED" in rapport en ga door
- Pipeline stopt niet — noteer de exacte fout in het eindrapport

---

## Phase 6: Merge

Sla over als `--no-merge` vlag aanwezig is.

**Als `--review` flag aanwezig:** toon eerst finale samenvatting en vraag goedkeuring:
```
AskUserQuestion:
  "Pipeline volledig voor {SPEC-ID} — {spec_title}.
   ✓ Implementatie  ✓ Interview review  ✓ Migraties  ✓ PR: {PR_URL}  [✓/⚠ E2E]
   Merge naar main?"
  Opties:
  - Mergen  |  PR bekijken (stop)  |  Bewaren voor later (stop)
```

**Zonder `--review` flag:** merge direct.

```bash
gh pr merge {PR_URL} --squash --delete-branch
git checkout main
git pull origin main
```

Verifieer:
- Exit code 0
- Branch verwijderd: `git branch -r | grep feature/{SPEC-ID}` → leeg

---

## Afronding

Toon altijd een eindrapport, ongeacht flags:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
/klai:auto {SPEC-ID} — {spec_title}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Implementatie
✓ Interview Standard: N issues auto-fixed [, K unresolved → zie PR]
✓ Migraties: [N gedraaid / geen / GEFAALD — zie PR]
✓ Sync & PR: {PR_URL}
[✓ E2E geslaagd / ⚠ E2E gefaald → zie PR / ⏭ E2E overgeslagen]
✓ Gemerged naar main

Volgende spec: /klai:auto {VOLGENDE-SPEC-ID}
Nieuwe spec schrijven: /moai plan
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Error Recovery

Als een phase faalt met een onverwachte error (niet hierboven gedekt):
- Rapporteer exact welke phase, welk commando, welke error
- Bewaar de branch in de huidige staat (geen destructieve acties)
- Stop pipeline
- Geef het herstelcommando om handmatig verder te gaan

---

## Configuratie

Lees voor git workflow uit `.moai/config/sections/git-strategy.yaml`:
- `branch_prefix`: voor branch naming
- `main_branch`: target voor merge
- `workflow`: github-flow / main_direct
