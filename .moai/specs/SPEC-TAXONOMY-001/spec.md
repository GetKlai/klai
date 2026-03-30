# SPEC-TAXONOMY-001: Assertion Mode Taxonomy Alignment

> Status: Completed
> Priority: MEDIUM
> Created: 2026-03-30
> Research: `docs/research/assertion-modes/assertion-modes-research.md`, `docs/research/assertion-modes/assertion-mode-weights.md`
> Architecture: `docs/architecture/klai-knowledge-architecture.md`
> Scope: `deploy/klai-knowledge-mcp/`, `deploy/knowledge-ingest/`, `klai-retrieval-api/`

---

## Context

De assertion mode taxonomy is inconsistent tussen twee componenten:

| Component | Geaccepteerde waarden | Bron |
|---|---|---|
| MCP interface (`klai-knowledge-mcp/main.py`) | `fact`, `claim`, `note` | 3 waarden, kort en LLM-vriendelijk; `note` is semantisch ongeschikt — het beschrijft een format ("aantekening"), geen epistemische houding |
| DB/ingest (`knowledge_ingest/routes/ingest.py`) | `factual`, `procedural`, `quoted`, `belief`, `hypothesis` | 5 waarden, uitgebreid maar moeilijk te classificeren |

Deze zijn VERSCHILLENDE NAMEN voor overlappende concepten. Er is geen mapping-laag; de MCP fallback mapt ongeldige waarden naar `note`, wat semantisch onjuist is (`note` verliest het onderscheid tussen procedureel, geciteerd en hypothetisch materiaal). Daarnaast is `note` zelf een slechte naam: het beschrijft een format ("aantekening"), geen epistemische houding — in tegenstelling tot `fact`, `claim`, `procedural` en `quoted` die elk een duidelijke zekerheidsgraad of content-type uitdrukken. Dit is een data-kwaliteitsbug.

Daarnaast is `assertion_mode` write-only vanuit retrieval-perspectief:
- **Qdrant payload:** `assertion_mode` ontbreekt in `_ALLOWED_METADATA_FIELDS` — het wordt niet opgeslagen in de Qdrant point payload, dus kan niet worden gefilterd of doorgestuurd
- **Retrieval API:** `search.py` geeft `assertion_mode` niet terug in het result dict — downstream scoring (SPEC-EVIDENCE-001) kan het niet gebruiken

De research (assertion-modes-research.md, sectie 5) laat zien:
- Human agreement bij 5 categorieën: ~67% (slecht, Rubin 2007)
- Human agreement bij 3 categorieën: ~89% (Prieto et al., 2020)
- De drie goed-onderscheidbare modes zijn: `procedural` (structuurmarkers), `quoted` (syntactische markers), `hypothesis` (hedging-markers)
- De moeilijke grens is `factual` vs. `belief` — zelfs expert-annotatoren zijn het oneens
- De MCP-keuze voor 3 waarden was pragmatisch correct, maar mist `procedural` en `quoted` die wél goed classificeerbaar zijn

---

## Goal

Eén geünificeerde assertion mode vocabulary in alle componenten, met MCP-stijl naamgeving als basis. Assertion mode wordt opgeslagen in Qdrant en doorgestuurd in retrieval-resultaten, zodat SPEC-EVIDENCE-001 het kan gebruiken. De waarden worden NIET gebruikt voor scoring in deze SPEC — dat is SPEC-EVIDENCE-002.

---

## Design Decisions

### DD-1: Geünificeerde vocabulary — 6 waarden, MCP-stijl naamgeving

**Keuze:** `fact`, `claim`, `speculation`, `procedural`, `quoted`, `unknown`

**Rationale:**

De MCP-stijl naamgeving (kort, beschrijvend, LLM-vriendelijk) wordt aangehouden. `unknown` wordt toegevoegd als zesde waarde én als system default voor ongetagde content. Dit is eerlijker dan `fact` als default: als de epistemische status niet expliciet is opgegeven, weten we het niet — en dat moet het systeem niet stilzwijgend als feit behandelen.

`unknown` is fundamenteel anders dan de andere vijf waarden: het is geen epistemische klasse, maar een expliciete erkenning dat de classificatie ontbreekt. Het verhoogt de inter-annotator agreement niet verder (elke annotator kan het eens zijn over "weet ik niet") en vermijdt het probleem van fout-positieve boosts in retrieval scoring.

De twee extra waarden (`procedural`, `quoted`) zijn wél goed classificeerbaar (structuurmarkers resp. syntactische markers) en gaan verloren bij een 5-waarden reductie.

| Nieuwe waarde | Vervangt (DB-oud) | Vervangt (MCP-oud) | Beschrijving |
|---|---|---|---|
| `fact` | `factual` | `fact` | Feitelijke bewering, vastgesteld of algemeen geaccepteerd |
| `claim` | `belief` | `claim` | Subjectieve bewering, mening, interpretatie |
| `speculation` | `hypothesis` | *(niet beschikbaar)* | Speculatief, hypothetisch, voorlopig |
| `procedural` | `procedural` | *(niet beschikbaar)* | Instructie, stappenbeschrijving, how-to |
| `quoted` | `quoted` | *(niet beschikbaar)* | Direct citaat, letterlijke aanhaling met bronvermelding |
| `unknown` | *(niet aanwezig)* | `note` (fallback) | Epistemische status niet bepaald; system default voor ongetagde content |

**Overwogen alternatieven:**

1. *5-waarden zonder `unknown`:* `fact` als default voor ongetagde content. Probleem: ongetagde content krijgt een onterechte boost in retrieval scoring (SPEC-EVIDENCE-002). `unknown` = flat/neutraal weight.
2. *DB-stijl naamgeving behouden (5 oude waarden):* Inconsistent met MCP die al in productie is; `factual` vs. `fact` is een onnodige complicatie.
3. *`note` behouden:* `note` beschrijft een format, geen epistemische houding. De semantiek van "aantekening" overlapt met alle vijf andere categorieën — elke aantekening is óf een feit, óf een mening, óf speculatie, etc.
4. *`hypothesis` behouden i.p.v. `speculation`:* Semantisch equivalent; `speculation` is gekozen als gewoner Engels woord.

### DD-2: `unknown` als default, niet `fact`

De ingest-route gebruikt momenteel `factual` (→ `fact`) als default voor content zonder frontmatter. Dit wordt gewijzigd naar `unknown`. Rationale: als geen assertion_mode is opgegeven, is de epistemische status onbekend — niet per definitie feitelijk. Door `fact` als default te gebruiken zouden alle ongetagde documenten een onterechte feit-boost krijgen bij retrieval scoring (SPEC-EVIDENCE-002). `unknown` krijgt flat/neutraal gewicht en zorgt niet voor fout-positieve boosts.

De MCP fallback naar `note` was een verdedigingskeuze maar maskeert datafouten — als de MCP nu alle 6 waarden accepteert, is de fallback niet meer nodig. Bij ontbrekende `assertion_mode` in een MCP-call wordt `unknown` gebruikt.

### DD-3: Type validatie — `Literal` + `frozenset` per service

De drie services (MCP, ingest, retrieval) zijn aparte deployments en kunnen geen Python-import delen. Per service wordt de vocabulary gedefinieerd als:

```python
from typing import Literal, get_args

AssertionMode = Literal["fact", "claim", "speculation", "procedural", "quoted", "unknown"]
VALID_ASSERTION_MODES: frozenset[str] = frozenset(get_args(AssertionMode))
```

Dit geeft type checking via `Literal` en runtime validatie via de `frozenset`, consistent met het bestaande patroon in de MCP (`VALID_ASSERTION_MODES = frozenset(...)`).

### DD-4: Scope-afbakening — geen scoring

Deze SPEC betreft uitsluitend:
- Vocabulaire-unificatie
- Opslag in Qdrant
- Doorgifte in retrieval-resultaten

Het GEBRUIK van `assertion_mode` als scoring-signaal valt onder SPEC-EVIDENCE-001 (plumbing met flat weights) en SPEC-EVIDENCE-002 (gewichten activeren na empirische validatie).

---

## Requirements (EARS)

### R1 — Geünificeerde vocabulary

**The system shall** in alle componenten uitsluitend de volgende `assertion_mode` waarden accepteren: `fact`, `claim`, `speculation`, `procedural`, `quoted`, `unknown`.

Getroffen bestanden:
- `deploy/klai-knowledge-mcp/main.py` — `VALID_ASSERTION_MODES`
- `deploy/knowledge-ingest/knowledge_ingest/routes/ingest.py` — `_parse_knowledge_fields()`
- `deploy/knowledge-ingest/knowledge_ingest/models.py` — type annotations

### R2 — MCP accepteert alle 6 waarden

**When** een MCP tool (`save_personal_knowledge`, `save_org_knowledge`) wordt aangeroepen, **the system shall** alle 6 assertion modes accepteren. Ongeldige waarden resulteren in een foutmelding, niet in een stille fallback. Bij ontbrekende `assertion_mode` wordt `unknown` gebruikt als default.

Huidige staat: `VALID_ASSERTION_MODES = frozenset({"fact", "claim", "note"})` — moet uitgebreid naar alle 6 met `note` verwijderd en `speculation` + `procedural` + `quoted` + `unknown` toegevoegd.

### R3 — Ingest accepteert nieuwe vocabulary

**When** content via de ingest-route wordt verwerkt, **the system shall** de nieuwe vocabulary (`fact`, `claim`, `speculation`, `procedural`, `quoted`, `unknown`) accepteren in YAML-frontmatter. De oude waarden (`factual`, `belief`, `hypothesis`, `note`) worden geaccepteerd en automatisch gemapt naar de nieuwe equivalenten. Ontbrekende frontmatter-tag resulteert in `unknown`.

Mapping:
- `factual` → `fact`
- `belief` → `claim`
- `hypothesis` → `speculation`
- `note` → `unknown` (oude MCP-fallback: epistemische status onbekend)
- `procedural` → `procedural` (ongewijzigd)
- `quoted` → `quoted` (ongewijzigd)
- *(geen tag)* → `unknown` (nieuwe default, vervangt `fact`)

### R4 — Assertion mode in Qdrant payload

**When** chunks worden opgeslagen in Qdrant, **the system shall** `assertion_mode` opnemen in de point payload.

Dit vereist twee wijzigingen:
1. `assertion_mode` toevoegen aan `extra_payload` in de ingest-route (zodat het naar `qdrant_store.upsert_chunks` wordt doorgegeven)
2. `assertion_mode` toevoegen aan `_ALLOWED_METADATA_FIELDS` in `qdrant_store.py` (zodat het niet wordt weggefilterd bij lezen)

### R5 — Assertion mode in retrieval-resultaten

**When** een Qdrant search chunks retourneert, **the system shall** `assertion_mode` opnemen in het result dict in `search.py`, zodat downstream scoring (SPEC-EVIDENCE-001) het kan gebruiken.

### R6 — PostgreSQL data-migratie (Alembic)

**The system shall** bestaande PostgreSQL `assertion_mode` waarden migreren naar de nieuwe vocabulary via een Alembic data-migratie:
- `factual` → `fact`
- `belief` → `claim`
- `hypothesis` → `speculation`
- `note` → `unknown` (oude MCP-fallback: epistemische status was onbekend)
- `procedural`, `quoted` → ongewijzigd
- `NULL` / ontbrekend → `unknown` (nieuwe default)

De migratie wordt als Alembic revision aangemaakt in `klai-portal/backend/alembic/versions/`, zodat het traceerbaar en herhaalbaar is op alle omgevingen.

### R7 — *(Geverifieerd: geen wijziging nodig)*

De MCP stuurt `assertion_mode` al correct mee in het `metadata` veld van het ingest-request via `_save_to_ingest()` ([main.py:127](deploy/klai-knowledge-mcp/main.py#L127)). Geen codewijziging nodig.

---

## Acceptance Criteria

- [x] `VALID_ASSERTION_MODES` in MCP bevat alle 6 waarden: `fact`, `claim`, `speculation`, `procedural`, `quoted`, `unknown`
- [x] MCP geeft een foutmelding bij ongeldige `assertion_mode`, geen stille fallback; default bij ontbrekende waarde is `unknown`
- [x] Ingest `_parse_knowledge_fields()` accepteert zowel nieuwe als oude waarden met mapping (`note` → `unknown`, geen frontmatter-tag → `unknown`)
- [x] `assertion_mode` staat in `extra_payload` bij Qdrant upsert
- [x] `assertion_mode` staat in `_ALLOWED_METADATA_FIELDS` in `qdrant_store.py`
- [x] `search.py` retourneert `assertion_mode` in het result dict
- [x] Data-migratie: `factual`→`fact`, `belief`→`claim`, `hypothesis`→`speculation`, `note`→`unknown`, `NULL`→`unknown` — als raw SQL in `deploy/postgres/migrations/010_assertion_mode_taxonomy.sql` (zie implementatienoot)
- [x] Type validatie via `Literal` + `frozenset` patroon in MCP, ingest en retrieval (6 waarden)
- [x] Bestaande tests passen voor nieuwe vocabulary
- [x] Geen regressie in MCP save-functionaliteit (handmatige test of unit test)
- [x] Geen regressie in ingest-route (bestaand frontmatter met oude waarden blijft werken)

---

## Architecture Fit

### Gewijzigde bestanden

| Bestand | Wijziging |
|---|---|
| `deploy/klai-knowledge-mcp/main.py` | `VALID_ASSERTION_MODES` uitbreiden naar 6 waarden (`note` verwijderd, `speculation`/`procedural`/`quoted`/`unknown` toegevoegd); fallback vervangen door error-return; default `unknown` bij ontbrekende waarde |
| `deploy/knowledge-ingest/knowledge_ingest/routes/ingest.py` | `_parse_knowledge_fields()`: nieuwe waarden accepteren + backward-compatible mapping; `assertion_mode` toevoegen aan `extra_payload` |
| `deploy/knowledge-ingest/knowledge_ingest/routes/personal.py` | Controleer of `assertion_mode` correct doorstroomt (waarschijnlijk al OK via `extra_payload`) |
| `deploy/knowledge-ingest/knowledge_ingest/qdrant_store.py` | `assertion_mode` toevoegen aan `_ALLOWED_METADATA_FIELDS` |
| `deploy/knowledge-ingest/knowledge_ingest/models.py` | `AssertionMode` Literal type + `VALID_ASSERTION_MODES` frozenset |
| `klai-retrieval-api/retrieval_api/services/search.py` | `assertion_mode` toevoegen aan result dict (`r.payload.get("assertion_mode")`) |
| `deploy/knowledge-ingest/knowledge_ingest/pg_store.py` | Geen codewijziging nodig — schrijft al `assertion_mode` als string. Migratie via SQL. |

### Dataflow na wijziging

```
MCP tool call (assertion_mode = "fact" | "claim" | "speculation" | "procedural" | "quoted" | "unknown")
  → POST /ingest (metadata.assertion_mode)
    → _parse_knowledge_fields() — accepteert nieuwe + mapt oude waarden
    → pg_store.upsert_artifact() — slaat op in PostgreSQL
    → extra_payload["assertion_mode"] = kf["assertion_mode"]  ← NIEUW
    → qdrant_store.upsert_chunks(extra_payload)
      → Qdrant point payload bevat assertion_mode  ← NIEUW

Retrieval:
  → Qdrant search
    → search.py result dict bevat assertion_mode  ← NIEUW
      → evidence_tier.py kan assertion_mode gebruiken (SPEC-EVIDENCE-001)
```

### Migratie (Alembic)

```python
# Alembic data-migratie (in upgrade())
op.execute("""
    UPDATE knowledge.artifacts
    SET assertion_mode = CASE assertion_mode
        WHEN 'factual' THEN 'fact'
        WHEN 'belief' THEN 'claim'
        WHEN 'hypothesis' THEN 'speculation'
        WHEN 'note' THEN 'unknown'
        ELSE assertion_mode  -- procedural, quoted blijven ongewijzigd
    END
    WHERE assertion_mode IN ('factual', 'belief', 'hypothesis', 'note')
""")

# NULL-waarden ook migreren naar 'unknown'
op.execute("""
    UPDATE knowledge.artifacts
    SET assertion_mode = 'unknown'
    WHERE assertion_mode IS NULL
""")
```

---

## Implementatievolgorde

| # | Taak | Risico |
|---|---|---|
| 1 | R4 + R5: `assertion_mode` toevoegen aan Qdrant payload + retrieval result | Laag — additief, breekt niets |
| 2 | R3: Ingest `_parse_knowledge_fields()` uitbreiden met nieuwe vocabulary + backward mapping | Laag — backward compatible |
| 3 | R1 + R2: MCP `VALID_ASSERTION_MODES` uitbreiden + error handling | Laag — strenger, niet losser |
| 4 | R6: Alembic data-migratie aanmaken en uitvoeren | Laag — data UPDATE, geen schema-wijziging |
| 5 | Tests bijwerken | Laag |

Stap 1-3 kunnen als één commit. Stap 4 (Alembic migratie) is een aparte commit en wordt via `alembic upgrade head` uitgevoerd op alle omgevingen.

---

## Wat bewust NIET in scope is

- Assertion mode als scoring-signaal activeren (SPEC-EVIDENCE-001: plumbing met flat weights)
- Assertion mode gewichten tunen (SPEC-EVIDENCE-002: na empirische validatie)
- HyPE-prompt aanpassen om assertion mode te classificeren (apart onderzoek)
- Frontend badge-weergave bijwerken (volgt automatisch als de API de nieuwe waarden retourneert)
- Qdrant re-indexering van bestaande chunks — niet nodig, de hele dataset wordt opnieuw geïngest (testfase)

---

## Risico's

| Risico | Mitigatie |
|---|---|
| Bestaande MCP-clients sturen oude waarden (`fact`/`claim`/`note`) | `fact` en `claim` zijn ongewijzigd; `note` wordt gemapt naar `unknown` via de backward-compatible mapping in R3 |
| Bestaande frontmatter gebruikt oude DB-waarden (`factual`, `belief`, `hypothesis`) | R3 bevat backward-compatible mapping — oude waarden worden automatisch vertaald |
| Qdrant bestaande chunks missen `assertion_mode` in payload | Acceptabel: `r.payload.get("assertion_mode")` retourneert `None`; evidence_tier (SPEC-EVIDENCE-001) behandelt `None` als flat weight |
| PostgreSQL-migratie op grote tabel | `knowledge.artifacts` is klein (<100K rijen); UPDATE met WHERE-clause is snel en safe |

---

## Afhankelijkheden

| SPEC | Relatie |
|---|---|
| SPEC-EVIDENCE-001 | Consument: R4 en R5 zijn prerequisites voor evidence tier scoring. Assertion mode plumbing met flat weights (R5 van SPEC-EVIDENCE-001) vereist dat de waarden in het result dict beschikbaar zijn. |
| SPEC-EVIDENCE-002 | Consument: gewichten activeren op assertion mode. Vereist dat de taxonomy stabiel en geünificeerd is. |

---

## Implementatienoten

> Toegevoegd na implementatie (commit `3348c16`, 2026-03-30)

### Afwijkingen van het plan

**R6 — Migratie locatie:** De SPEC specificeerde een Alembic revision in `klai-portal/backend/alembic/versions/`. De implementatie gebruikte raw SQL in `deploy/postgres/migrations/010_assertion_mode_taxonomy.sql`, consistent met het bestaande migratiepatroon in de repository. Functioneel equivalent; de SQL is idempotent (DROP IF EXISTS + UPDATE WHERE).

**R4 + R5 — Al aanwezig:** `assertion_mode` stond al in `_ALLOWED_METADATA_FIELDS` (qdrant_store.py) en in het result dict (search.py). De SPEC ging ervan uit dat deze ontbraken; in werkelijkheid was alleen de `extra_payload` doorgifte in ingest.py nieuw.

### Bestanden gewijzigd

| Bestand | Type | Omschrijving |
|---|---|---|
| `deploy/klai-knowledge-mcp/main.py` | Gewijzigd | 6-waarden taxonomy, `unknown` als default, foutmelding bij ongeldig |
| `deploy/knowledge-ingest/knowledge_ingest/models.py` | Gewijzigd | `AssertionMode` Literal + `VALID_ASSERTION_MODES` |
| `deploy/knowledge-ingest/knowledge_ingest/routes/ingest.py` | Gewijzigd | Backward-compat mapping, `assertion_mode` in `extra_payload` |
| `deploy/postgres/migrations/010_assertion_mode_taxonomy.sql` | Nieuw | Idempotente data-migratie + CHECK constraint |
| `deploy/klai-knowledge-mcp/tests/test_assertion_mode_taxonomy.py` | Nieuw | 11 specificatietests |
| `deploy/knowledge-ingest/tests/test_assertion_mode_taxonomy.py` | Nieuw | 12 specificatietests |
| `klai-retrieval-api/tests/test_assertion_mode_taxonomy.py` | Nieuw | 7 specificatietests |

### Testresultaten

- klai-knowledge-mcp: 20/20 passing
- knowledge-ingest: 178/178 passing
- klai-retrieval-api: 52/52 passing
- TRUST 5: PASS (97%)

### Openstaand

- PostgreSQL-migratie `010_assertion_mode_taxonomy.sql` moet nog worden uitgevoerd op productie.

---

## Bronnen

- Prieto et al. (2020). Data-driven classification of the certainty of scholarly assertions. PeerJ. — [peerj.com/articles/8871](https://peerj.com/articles/8871/)
- Rubin (2007). Stating with Certainty or Stating with Doubt. NAACL. — [aclanthology.org/N07-2036](https://aclanthology.org/N07-2036/)
- Interne research: `docs/research/assertion-modes/assertion-modes-research.md`
- Interne research: `docs/research/assertion-modes/assertion-mode-weights.md`
