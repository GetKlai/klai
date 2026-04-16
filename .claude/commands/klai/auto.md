---
description: >
  Full autonomous pipeline voor een goedgekeurde SPEC: run → interview-standard review →
  migraties → sync → e2e → finale check → merge. De SPEC annotation stop (plan fase) is
  bewust NIET opgenomen — schrijf eerst je specs, dan run je per spec /klai:auto.
argument-hint: "SPEC-XXX [--no-e2e] [--no-merge] [--no-issue]"
---

# /klai:auto — Klai Autonomous Pipeline

**Input**: $ARGUMENTS (SPEC-ID verplicht, bijv. `SPEC-KB-003`)

Pre-execution git context:
!`git branch --show-current 2>/dev/null`
!`git status --porcelain 2>/dev/null | head -10`

---

## Doel

Voer de complete post-SPEC cyclus uit voor een al goedgekeurde SPEC. De SPEC annotation
(plan fase) heeft de gebruiker al gedaan. Dit commando start bij de implementatie.

**Flags:**
- `--no-e2e`: Sla E2E tests over (bijv. als dev server niet draait)
- `--no-merge`: Stop na sync + E2E, merge niet automatisch
- `--no-issue`: Geen GitHub Issue aanmaken

**Volledige flow:**
```
run → interview-standard gate → migraties → sync --pr → e2e → finale gate → merge
```

**Twee menselijke stops:**
1. **Interview Standard Gate** — na run, voor sync: review + approve kwaliteit
2. **Finale Gate** — na sync + e2e, voor merge: laatste check voor main

---

## Phase 0: SPEC Verificatie

Lees `.moai/specs/{SPEC-ID}/spec.md` om te bevestigen dat de SPEC bestaat en al gecheckt is.

Als de SPEC niet bestaat: geef een foutmelding en stop.

Bepaal:
- `spec_title`: de titel van de SPEC
- `branch_name`: `feature/{SPEC-ID}` (lees uit git-strategy.yaml)
- `has_e2e`: true tenzij `--no-e2e` vlag aanwezig

---

## Phase 1: Implementatie

Delegeer aan MoAI run workflow:

Roep de run workflow aan via `Skill("moai:run")` met het SPEC-ID als argument en
`--solo` mode. Wacht op de completion marker `<moai>DONE</moai>` of `<moai>COMPLETE</moai>`.

Als run mislukt (CRITICAL kwaliteitsproblemen of test failures): presenteer de
samenvatting en stop. Laat de gebruiker het probleem handmatig oplossen.

---

## Phase 2: Interview Standard Gate (MENSELIJKE STOP)

**Dit is de kern van /klai:auto.** Voer een grondige kwaliteitsreview uit met
een hogere lat dan standaard TRUST 5.

### Phase 2.1: Interview Standard Review

Delegeer aan `manager-quality` subagent met de volgende specifieke focus:

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

### Phase 2.2: Auto-fix CRITICAL en IMPORTANT Issues

Voor elke CRITICAL en IMPORTANT bevinding:
- Delegeer aan `expert-refactoring` subagent met de specifieke bevinding
- Verifieer dat de fix correcte tests passeert
- Maximum 3 auto-fix rondes per bevinding
- SUGGESTION bevindingen: log maar fix niet automatisch

### Phase 2.3: Interview Standard Goedkeuring

Presenteer aan de gebruiker:
- Aantal CRITICAL gevonden: N (auto-fixed: M)
- Aantal IMPORTANT gevonden: N (auto-fixed: M)
- Suggesties (niet auto-fixed): lijst
- Eventuele resterende issues na auto-fix

```
AskUserQuestion:
  "Interview Standard Review voltooid. [Samenvatting].
   Doorgaan naar sync en push naar main?"
  Opties:
  - Doorgaan → fase 3 (migraties)
  - Nog meer refactoren → geef specifieke instructie, ga terug naar 2.1
  - Stoppen → bewaar branch, stop pipeline
```

---

## Phase 3: Migratie Check & Run

**Achtergrond:** Alembic migraties worden bijna nooit gedraaid in de workflow
terwijl ze wel aangemaakt worden. Dit lost dat op.

### Phase 3.1: Detecteer nieuwe migraties

```bash
git diff main...HEAD --name-only -- "klai-portal/backend/alembic/versions/*.py"
```

Als geen migraties gevonden: sla Phase 3.2 en 3.3 over.

### Phase 3.2: Valideer migraties

Voor elke nieuwe migratiefile:
- Controleer of `down_revision` klopt (nooit handmatig getypt — zie deploy.md)
- Check op conflicterende heads: `docker exec portal-api alembic heads 2>/dev/null ||
  cd klai-portal/backend && uv run alembic heads`
- Als meerdere heads: voer `alembic merge heads` uit VOOR upgrade

### Phase 3.3: Draai migraties (lokale dev)

Probeer in volgorde:
1. Als Docker container draait: `docker exec portal-api alembic upgrade head`
2. Als niet in Docker: `cd klai-portal/backend && uv run alembic upgrade head`

Verifieer succes:
- Exit code 0
- Geen "Multiple head revisions" errors

Als migratie faalt:
- Presenteer de foutmelding
- Gebruik AskUserQuestion: "Migratie mislukt. Wil je het handmatig oplossen
  of de migratie overslaan en doorgaan?"

### Phase 3.4: Migratie samenvatting

Voeg toe aan sync rapport: "N nieuwe migratie(s) succesvol gedraaid."

---

## Phase 4: Sync & PR

Delegeer aan sync workflow via `Skill("moai:sync")` met `--pr` vlag
(en `--no-issue` als die vlag meegegeven is).

Wacht op:
- PR URL in output
- Sync completion marker

Sla de PR URL op voor gebruik in Phase 6.

---

## Phase 5: E2E Tests

Sla over als `--no-e2e` vlag aanwezig is.

Delegeer aan E2E workflow via `Skill("moai:e2e")` met:
- `--tool playwright` (voorkeur voor Klai CI)
- `--headless`

Focus de E2E tests op de user journeys die door deze SPEC geïmplementeerd zijn.
Lees de acceptance criteria uit `spec.md` om te bepalen welke flows getest worden.

Als E2E tests falen:
```
AskUserQuestion:
  "E2E tests gefaald: [beschrijving mislukte tests].
   Wat wil je doen?"
  Opties:
  - Auto-fix en opnieuw draaien
  - Handmatig bekijken (stop pipeline hier)
  - Negeren en doorgaan naar merge
```

---

## Phase 6: Finale Gate (MENSELIJKE STOP)

De laatste check voor de code naar main gaat.

### Phase 6.1: Finale Interview Standard Scan

Delegeer snel aan `manager-quality` voor een finale scan van de volledige diff:

> "Geef een snelle finale check van alle wijzigingen op deze branch. Zijn er
> nog CRITICAL issues die je in Phase 2 gemist hebt? Kijk specifiek naar de
> interactie tussen bestanden — niets hoeft opgelost te worden, alleen flaggen
> als er nog showstoppers zijn."

### Phase 6.2: Finale Goedkeuring

```
AskUserQuestion:
  "Pipeline volledig doorlopen voor {SPEC-ID} — {spec_title}.
   
   Samenvatting:
   ✓ Implementatie compleet
   ✓ Interview Standard goedgekeurd
   ✓ Migraties gedraaid (of: geen migraties)
   ✓ Sync & PR aangemaakt: {PR_URL}
   [✓ E2E tests geslaagd / ⚠ E2E overgeslagen]
   
   Finale scan: [CLEAN / N suggesties]
   
   Merge naar main?"
  Opties:
  - Mergen → phase 7
  - PR eerst reviewen → open PR URL, stop hier
  - Stoppen → bewaar PR voor later
```

---

## Phase 7: Merge (tenzij --no-merge of gebruiker kiest stop)

```bash
gh pr merge {PR_URL} --squash --delete-branch
git checkout main
git pull origin main
```

Verifieer:
- Branch verwijderd
- main up to date

Samenvatting aan gebruiker:
```
✓ {SPEC-ID} gemerged naar main
Branch: feature/{SPEC-ID} opgeruimd
Volgende: /klai:auto {VOLGENDE-SPEC-ID} of /moai plan voor nieuwe SPEC
```

---

## Error Recovery

Als een phase faalt met een onverwachte error:
- Rapporteer exact wat misging en in welke phase
- Geef het exacte commando dat gefaald heeft
- Bewaar de branch in de huidige staat
- Geef herstel-instructies
- Stop pipeline (geen destructieve acties na een failure)

---

## Configuratie

Lees voor git workflow uit `.moai/config/sections/git-strategy.yaml`:
- `branch_prefix`: voor branch naming
- `main_branch`: target voor merge
- `workflow`: github-flow / main_direct
