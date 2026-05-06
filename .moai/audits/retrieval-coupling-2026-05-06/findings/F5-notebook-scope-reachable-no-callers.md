# F5 — Notebook scope: reachable code, geen actieve callers

**Severity:** INFO — code hygiene
**Status:** ✅ ALREADY-DONE — closed by `SPEC-DECOMM-FOCUS-001` (#368, commit `e6fabc73`, 2026-05-05).

## Update na audit (2026-05-06)

Het cleanup-werk dat de F5-agent voorstelde (Optie A) was al **vóór deze audit** geïmplementeerd in `SPEC-DECOMM-FOCUS-001` (PR #368, 2026-05-05). Geverifieerd op `origin/main` (HEAD = `2ac1b359`):

- ✅ `_search_notebook`, `_notebook_filter` verwijderd uit `retrieval-api/services/search.py`
- ✅ `qdrant_focus_collection` setting verwijderd uit `config.py`
- ✅ `Literal["notebook", "broad"]` verwijderd uit `models.py`
- ✅ `notebook_id` veld verwijderd
- ✅ Alle `if req.scope == "notebook"` / `"broad"` branches in `retrieve.py` + `chat.py` verwijderd
- ✅ `klai_focus` tuple verwijderd uit `provisioning/deprovisioning_steps.py:246` (comment annoteert SPEC-DECOMM-FOCUS-001)
- ✅ `research-api` verwijderd uit `KNOWN_CALLER_SERVICES` (klai-libs/identity-assert + portal-api/identity_verifier — comments documenteren removal)
- ✅ `klai_focus` Qdrant collection gedropped op prod (manueel via runbook in PR #368)
- ✅ klai-focus directory volledig verwijderd

De originele audit was uitgevoerd op een working tree die forkte vóór `e6fabc73`, dus de F5 finding zag een verouderde code-state. Geen verdere actie nodig.

---

## Originele finding (preserved for reference)

## Initial finding

retrieval-api ondersteunt `scope="notebook"` (en "broad"), met dedicated codepaden:

- [`models.py:15`](../../../klai-retrieval-api/retrieval_api/models.py#L15): scope literal omvat `"notebook"` en `"broad"`
- [`retrieve.py:79-86`](../../../klai-retrieval-api/retrieval_api/api/retrieve.py#L79-L86): notebook-specifieke validation (notebook_id + user_id required)
- [`search.py:115-220`](../../../klai-retrieval-api/retrieval_api/services/search.py#L115-L220): `_search_notebook` op `qdrant_focus_collection` met `_notebook_filter` (visibility gate)
- [`config.py:13`](../../../klai-retrieval-api/retrieval_api/config.py#L13): `qdrant_focus_collection: str = "klai_focus"`
- [`search.py:419-444`](../../../klai-retrieval-api/retrieval_api/services/search.py#L419-L444): broad-scope merge tussen klai_knowledge en klai_focus

**Enige caller van notebook/broad scope** was klai-focus research-api (`retrieve_narrow` / `retrieve_broad`). Maar:

- klai-focus is in HEAD maar **niet in `deploy/docker-compose.yml`**
- **Niet running op core-01** (`docker ps | grep -i focus` leeg)
- 0 imports van buitenaf naar `klai-focus/research-api`-modules

Dus: notebook/broad scope code is wel uitvoerbaar maar wordt door geen enkele live service aangeroepen. Bestanden in retrieval-api dragen vanaf nu maintenance-cost zonder waarde.

## Open vragen voor verificatie

1. Klopt het dat geen enkele live service `scope="notebook"` of `scope="broad"` aanroept? Grep alle services in compose.
2. Welke kant gaat klai-focus op? Definitief verwijderen, gepauzeerd-houden voor revival, of ergens een herontwerp?
3. Als klai-focus dood is: kan `klai_focus` Qdrant collection ook weg? `provisioning/deprovisioning_steps.py:237` doet alleen DROP bij tenant-delete. Zijn er nog actieve klai_focus collections op prod? Check via Qdrant API.
4. Wat is de impact van scope-cleanup op tests? Hoeveel tests gebruiken notebook/broad scope als fixture?

## Voorgestelde aanpak (voor agent te valideren)

**Optie A — Definitief deleten** (als klai-focus nooit terugkomt):
- Verwijder `klai-focus/` directory
- Verwijder uit retrieval-api: `_search_notebook`, `_notebook_filter`, broad-scope merge in `hybrid_search`, `qdrant_focus_collection` setting
- Verwijder uit `models.py`: `"notebook"` en `"broad"` uit scope Literal
- Migratie: drop `klai_focus` Qdrant collection
- SPEC: ergens onder `SPEC-DEPRECATE-FOCUS-001` of als wave-1 cleanup

**Optie B — Gepauzeerd houden:**
- Toevoegen aan `.codeindexignore` zodat klai-focus uit toekomstige analyses valt
- README.md in klai-focus root: "PAUSED — code preserved, not deployed"
- Code in retrieval-api blijft staan (mogelijk inactief maar bereikbaar)
- Kosten: maintenance-overhead (test-fixtures, security audits, dependency-updates blijven applicabel)

## Verification

**Status:** CONFIRMED — notebook/broad scope is reachable code with zero live callers. klai-focus is decommissioned per SPEC-PORTAL-UNIFY-KB-001 (Phase C, commit `998f6f71`, 2026-04-23).

### 1. Caller-cross-check — every service in `deploy/docker-compose.yml`

Greppen over alle service-bronbomen op `scope.*(notebook|broad)` en `/retrieve`:

| Service | `/retrieve` caller? | `scope=` value(s) used |
|---|---|---|
| portal-api (`klai-portal/backend`) | YES — `services/partner_chat.py:111`, `services/gap_rescorer.py:110` | `"org"` only |
| LiteLLM hook (`deploy/litellm/klai_knowledge.py`) | YES — `klai_knowledge.py:199,212` | `"personal"`, `"both"`, `"org"` (line 1225/1228; never `notebook` or `broad`) |
| knowledge-ingest RAGAS harness (`klai-knowledge-ingest/.../eval/retrieval_client.py:83`) | YES (eval-only) | scope-default `"org"` (no override) |
| klai-connector | NO — only doc-references in comments |
| klai-mailer | NO — only doc-references in comments |
| klai-knowledge-mcp | NO — own MCP surface, doesn't call `/retrieve` |
| scribe-api | NO — only mentions retrieval-api in auth-config comment |
| runtime-api / api-gateway / admin-api / meeting-api | NO — Vexa stack, no retrieval coupling |
| librechat-getklai | NO — talks to LiteLLM, not directly to retrieval-api |
| klai-widget | NO |

`grep -rn 'scope.*notebook\|scope.*broad' --include='*.py' --include='*.ts' [services]` returns **only matches inside `klai-retrieval-api/` itself** (definitions + tests). Zero live callers in any service still in `docker-compose.yml`.

The only repo-level caller of `scope="notebook"` / `scope="broad"` is `klai-focus/research-api/app/services/retrieval_client.py:70,111` — which is in the FROZEN tree.

### 2. klai-focus deployment status — definitively dead

- **`docker ps -a` op core-01:** `grep -iE 'focus|research|notebook'` → empty. Zero containers, also not stopped/exited. (42 total containers running on core-01; none related to focus.)
- **`deploy/docker-compose.yml`:** geen `research-api` of `klai-focus` service-blok. Bevestigd door `deploy/VERSIONS.md:7`: *"research-api removed in SPEC-PORTAL-UNIFY-KB-001"*.
- **GitHub Actions:** `ls .github/workflows/ | grep -iE 'focus|research'` → leeg. Geen `*focus*.yml` / `*research*.yml` deploy-pijplijn. Service kan dus ook niet worden ge(re)deployed via CI.
- **Geen ssh-only deploy-script:** `grep -rn 'research-api' deploy/` levert alleen `deploy/VERSIONS.md` (decommission-note) en `deploy/caddy/Caddyfile:310` (decommission-comment) op. Geen handmatig deploy-pad.
- **klai-focus README:** `klai-focus/README.md` opent met `# FROZEN — replaced by Knowledge per SPEC-PORTAL-UNIFY-KB-001` en zegt expliciet *"Kept in the git tree for historical reference only. Do not resurrect."*

SPEC-PORTAL-UNIFY-KB-001 noemt `SPEC-RESEARCH-API-ARCHIVE-001` als toekomstige optionele follow-up *"mocht de `klai-focus/` submodule ooit volledig uit de tree worden verwijderd"* — die SPEC bestaat nog niet, en is ook niet nodig om de retrieval-api kant te schoonmaken (de twee zijn ontkoppeld).

### 3. Qdrant collection state op prod

```
GET http://qdrant:6333/collections          → {"collections":[{"name":"klai_knowledge"}]}
GET http://qdrant:6333/collections/klai_focus → {"status":{"error":"Not found: Collection `klai_focus` doesn't exist!"}}
```

**De `klai_focus` collection bestaat niet meer op prod.** Geen actieve tenants met klai_focus chunks. Geen point-count nodig — er is letterlijk niets om weg te gooien.

Bij-effect: `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:268` itereert nog steeds over `("klai_focus", "tenant_id")` als deel van de tenant-purge. Dat code-pad logt "qdrant_collection_not_found" en gaat door (idempotent), dus prod faalt niet — maar het is dode code geworden.

### 4. Test-impact

`grep -rn "scope.*notebook\|scope.*broad\|qdrant_focus_collection\|_search_notebook\|_notebook_filter\|klai_focus" klai-retrieval-api/tests/`:

| File | Hits | Total lines | Action |
|---|---|---|---|
| `tests/test_notebook_filter.py` | 9 | 114 | DELETE entire file |
| `tests/test_api.py` | 11 | 464 | Remove ~3 test functies (notebook validation, broad merge) |
| `tests/test_search.py` | 4 | 170 | Remove `_search_notebook`/`_notebook_filter` testjes |
| `tests/test_search_error_handling.py` | 3 | 259 | Remove notebook-error-path testjes |
| `tests/test_assertion_mode_taxonomy.py` | 1 | 79 | 1-line scope-fixture aanpassen |

Totaal: **5 test-bestanden, 28 hits**. Eén volledig file weg, vier files gedeeltelijk opgeschoond. Niet groot.

### 5. Decision research — bestaande SPECs

- **SPEC-PORTAL-UNIFY-KB-001 (Phase C, 2026-04-23, GA):** decommissioned `research-api` ten gunste van een unified Knowledge surface. Status: shipped. Bewust besloten "Focus-data wordt niet gemigreerd — research-api volledig decommission (hard)".
- **SPEC-SEC-AUDIT-2026-04 spec.md:318:** klai-focus is een "governance call, not a SPEC" — twee opties zijn al genoemd: (a) directory deleten, (b) HARD-rule tegen resurrection.
- **`SPEC-RESEARCH-API-ARCHIVE-001`:** referenced as toekomstige follow-up, niet aangemaakt.
- **Geen revival-signalen** in roadmap of recente SPECs. Notebook-scope is een artefact van de pre-unify architectuur.

### 6. Aanbeveling — Optie A (deleten, beperkte scope)

**Optie A — Definitief deleten van notebook/broad scope uit retrieval-api.**

Motivatie:
- klai-focus is dood, decommission staat in een geshipte SPEC, geen revival-signaal.
- klai_focus collection bestaat niet meer op prod → geen data-migratierisico.
- 0 live callers → geen runtime-impact.
- Code is bereikbaar via de openbare `/retrieve`-API en draagt review-, security-audit- en test-onderhoudslast.
- Test-cleanup is klein (1 file weg, 4 files gedeeltelijk).
- Optie B (gepauzeerd) is hier slecht: notebook-scope is geen klai-focus interne code maar een actieve API-contract-tak van retrieval-api. Een README in klai-focus verandert daar niets aan.

**Beperking:** "Optie A" beperkt zich tot retrieval-api + portal-api deprovisioning. Het volledig verwijderen van `klai-focus/` directory (zoals voorgesteld in de oorspronkelijke Optie A) is een aparte governance-call (zie SPEC-RESEARCH-API-ARCHIVE-001 placeholder) en hoeft NIET met deze finding mee. De FROZEN README in klai-focus is genoeg signaal voor het submodule.

## Recommended fix

**SPEC-RETRIEVAL-NOTEBOOK-SCOPE-REMOVE-001** (kleine cleanup-SPEC, één PR):

### klai-retrieval-api delete-list

| File | Delete |
|---|---|
| `retrieval_api/models.py` L15 | `"notebook"`, `"broad"` uit scope `Literal` halen |
| `retrieval_api/models.py` L17 | `notebook_id: str \| None = None` field weg |
| `retrieval_api/api/retrieve.py` L79-86 | notebook-validatie weg (notebook_id, user_id required gates) |
| `retrieval_api/api/retrieve.py` L177, 205, 246, 411 | `req.scope != "notebook"` guards weg (skip-graphiti, skip-link-expand, skip-rerank, skip-product-event); de bodies blijven, alleen de guard valt weg |
| `retrieval_api/api/chat.py` L28-29, L64 | idem voor chat endpoint |
| `retrieval_api/services/search.py` L115-220 | `_notebook_filter`, `_search_notebook` volledig weg (incl. visibility gate logica) |
| `retrieval_api/services/search.py` L419-444 | `if scope == "notebook"` en `if scope == "broad"` branches in `hybrid_search` weg (alleen `personal/org/both` over) |
| `retrieval_api/config.py` L13 | `qdrant_focus_collection` setting weg |
| `retrieval_api/config.py` L123 | comment over "focus" caller bijwerken |

### klai-retrieval-api tests

| File | Action |
|---|---|
| `tests/test_notebook_filter.py` | DELETE (114 regels, 9 hits) |
| `tests/test_search.py` | Remove notebook/broad-specifieke testjes (4 hits) |
| `tests/test_api.py` | Remove notebook/broad endpoint-validatie tests (~11 hits, 3 testfuncties geschat) |
| `tests/test_search_error_handling.py` | Remove notebook-pad error tests (3 hits) |
| `tests/test_assertion_mode_taxonomy.py` | Update 1 fixture-regel |

### klai-portal cleanup

| File | Action |
|---|---|
| `backend/app/services/provisioning/deprovisioning_steps.py` L233-300 (step 8 `_delete_qdrant_points`) | `("klai_focus", "tenant_id")` tuple uit `collections` lijst halen; doc-strings + comments bijwerken (G4 referentie naar twee-collections-pattern weg). Test in `test_deprovisioning_*` bijwerken indien aanwezig |

### Documentation cleanup (optional in zelfde PR, anders follow-up)

| File | Action |
|---|---|
| `docs/architecture/knowledge-ingest-flow.md` (regels 798-979) | Notebook/broad scope-tabellen + "Part 5: Klai Focus" sectie weg |
| `docs/architecture/platform.md:132` | `research-api` van core-01 service-lijst |
| `docs/runbooks/credential-rotation.md`, `litellm-retrieval-failed.md`, `gpu-01-setup.md` | Strip research-api / klai-focus referenties |

### Qdrant migratie

Geen. De collection bestaat niet meer (geverifieerd 2026-05-06). Niets te droppen.

### Niet in scope van deze SPEC

- `klai-focus/` directory blijft staan met de bestaande FROZEN README (per SPEC-PORTAL-UNIFY-KB-001 design). Volledige verwijdering valt onder de toekomstige `SPEC-RESEARCH-API-ARCHIVE-001`.

### PR-grootte schatting

~10-15 source-files aangeraakt, 1 testfile weg + ~4 testfiles getrimd, +1 deprovisioning-step bijgewerkt. Single PR, single SPEC-ID, een ochtend werk + CI-cycle.

## Risk if not fixed

- **Maintenance overhead permanent:** `_search_notebook` en `_notebook_filter` blijven security-audit-scope houden. SPEC-SEC-IDENTITY-ASSERT-001 REQ-5 visibility-gate in `_notebook_filter` is een non-trivial stuk auth-logica die getest, gereviewd en bij elke security-audit opnieuw beoordeeld wordt — voor een feature die niemand gebruikt.
- **API-contract surface:** `/retrieve` blijft `scope="notebook"` en `scope="broad"` accepteren. Een nieuw geschreven LLM-tool of een externe partner-integratie kan deze waarden ontdekken via API-introspectie / OpenAPI schema en beginnen te gebruiken — wat dan een verborgen revival forceert.
- **Confusing dead code:** nieuwe ontwikkelaars die de retrieval-api leren kennen verspillen tijd om "wat is notebook-scope, waar wordt het gebruikt" uit te zoeken. Eind 2026-05-06 audit tijd: ~30 min per persoon.
- **GDPR-purge dead branch:** `deprovisioning_steps.py` step 8 itereert nog over `klai_focus`. Bij elke tenant-delete log je `qdrant_collection_not_found` voor klai_focus. Dat is harmless maar misleidend — bij future bugs op step 8 verdwaalt de oncall in irrelevante "klai_focus not found" lines.
- **Test-fixture rot:** 28 test-hits over 5 files vergroten test-suite zonder waarde. Re-runs duren marginaal langer; refactors van retrieval-search worden duurder omdat je notebook-paden ook nog meeneemt.
- **Severity blijft INFO** — geen security- of correctheids-risico. Dit is pure code-hygiëne. Maar de cost-of-inaction is niet 0: de surface groeit, niet krimpt, zo lang het blijft staan.
