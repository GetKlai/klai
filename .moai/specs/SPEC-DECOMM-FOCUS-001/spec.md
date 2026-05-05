---
id: SPEC-DECOMM-FOCUS-001
version: "1.2"
status: implemented
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: medium
issue_number: 368
---

# SPEC-DECOMM-FOCUS-001: Klai Focus / research-api volledig opruimen

## HISTORY

| Version | Date | Author | Change |
|---------|------|--------|--------|
| 1.0 | 2026-05-05 | Mark Vletter | Initial draft. Verificatie tegen productie (core-01) bevestigt dat de service al ~14 dagen niet meer draait en geen traffic meer ontvangt; de opruim die SPEC-PORTAL-UNIFY-KB-001 (April 2026) startte, is nooit afgemaakt. |
| 1.1 | 2026-05-05 | Mark Vletter | `scope=broad` toegevoegd aan opruim-scope. v1.0-analyse keek alleen naar `_search_notebook` en zag niet dat `scope=broad` in `services/search.py:422` óók `_search_notebook` callt (parallel-merge tegen `klai_focus`). Verificatie: 0 callers in productie-code buiten klai-focus, 0 hits in retrieval-api logs in 24u. Plus `docs/architecture/knowledge-ingest-flow.md` (regels 799/843/991/994) die `broad` documenteren — toegevoegd aan E. |
| 1.2 | 2026-05-05 | Mark Vletter | **IMPLEMENTED.** Alle stappen voltooid op 2026-05-05: GetKlai/klai#368 (squash-merged commit `e6fabc73`) + GetKlai/klai-infra#5 (squash-merged commit `922cf035`). Runbook handmatig uitgevoerd op core-01: Qdrant `klai_focus` collection gedropped (idempotent, status `ok`), `/opt/klai/research-uploads/` + `/opt/klai/research-api-src/` weggegooid, `focus.legacy_data_purged` event geëmit (product_events row id=476, properties bevatten point_count=15, pdf_count=2, tenant_id=362757920133283846, spec=SPEC-DECOMM-FOCUS-001). Volledige acceptance D.1–D.6 groen. **SOPS sync workflow-issue:** auto-sync weigerde de 2 vars te verwijderen uit `/opt/klai/.env`; manueel via `sed -i` opgelost als tijdelijke workaround. Duurzame fix in vervolg-PR GetKlai/klai-infra#6 (squash-merged commit) — voegt `workflow_dispatch.inputs.allow_removal` toe aan `sync-env.yml` zodat toekomstige key-removals via `gh workflow run sync-env.yml -f allow_removal=I-CONFIRM-REMOVAL` netjes kunnen lopen. |

---

## Context

SPEC-PORTAL-UNIFY-KB-001 (geïmplementeerd 2026-04-23) heeft Focus en Knowledge gecollapsed tot één surface (`/app/knowledge`). Phase C verwijderde `research-api` uit de hoofd-`docker-compose.yml`, en de frontend redirect `/app/focus/*` → `/app/knowledge` is live.

Twee weken later staan er nog acht categorieën stille residu's verspreid over zes services en twee infrastructuur-locaties. Een audit op 2026-05-05 — getriggerd doordat PR #311 (caller-service hotfix) `research-api` op 2026-05-05 toevoegde aan `KNOWN_CALLER_SERVICES` zonder te verifiëren dat de service draait — bracht het volgende beeld:

### Productie-status (geverifieerd 2026-05-05)

| Check | Resultaat |
|---|---|
| `docker ps` core-01 | Geen `klai-core-research-api-*` container |
| `/opt/klai/docker-compose.yml` | 0 service-refs (alleen 2 SPEC-md comments) |
| Caddy `/research/*` requests laatste 7d | 0 |
| `retrieval-api` logs `_search_notebook` 24u | 0 hits |
| Qdrant `klai_focus` collection | 15 points (tenant `362757920133283846`, mtime 2026-03-25, geen UI-pad) |
| `/opt/klai/research-uploads/` | 2 PDFs (4 MB), zelfde tenant, 2026-03-25 |

### Stale references die het beeld vertroebelen

1. **Allowlists** — `klai-libs/identity-assert/klai_identity_assert/models.py` (KNOWN_CALLER_SERVICES bevat `research-api`, vandaag toegevoegd), `klai-libs/service-auth/klai_service_auth/scopes.py` (`svc-research-api` scope grant), `klai-libs/image-storage/klai_image_storage/url_guard.py` SSRF-allowlist (`research-api` + `klai-focus`), portal-api `services/identity_verifier.py` (gemirrord), portal-api `services/source_extractors/_url_validator.py`, retrieval-api `middleware/auth.py`. Plus tests in elk van die repo's.
2. **Dode code in retrieval-api** — `_search_notebook`, `_notebook_filter`, `qdrant_focus_collection`, `scope="notebook"` literal. Geen actieve caller meer.
3. **Provisioning** — `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:247` heeft `collections = ["klai_knowledge", "klai_focus"]`.
4. **SOPS** — `klai-infra/core-01/.env.sops`: `KUMA_TOKEN_RESEARCH_API`, `RESEARCH_API_ZITADEL_AUDIENCE`.
5. **Server-state** — `/opt/klai/research-uploads/` (2 PDFs), `/opt/klai/research-api-src/` (sync-residu), Qdrant `klai_focus` collection (15 points).
6. **Docs** — `klai-infra/SERVERS.md` regels 71/126/137/228/229 claimen "up, via /research/*" — feitelijk onjuist sinds Phase C.
7. **CI** — `klai-focus/.github/workflows/research-api.yml` blijft elke push een image bouwen + naar core-01 deployen (no-op zolang er geen compose-entry is, maar verspilling + verwarring).
8. **Tree** — `klai-focus/` directory (~3.5k regels, 41 files) staat als FROZEN per README; nooit verwijderd.

## Problem Statement

De halve decommission veroorzaakt drie problemen:

### Het ziet er levend uit

Een nieuwe maintainer (mens of agent) ziet de allowlist-entry van vandaag, leest SERVERS.md die zegt "up", vindt 41 files in `klai-focus/`, en concludeert ten onrechte dat het systeem operationeel is. PR #311 (vandaag) is daar het bewijs van: een fix werd toegepast op `klai-focus/research-api/app/services/retrieval_client.py` — dode code op een dode service.

### De opruim is bij iedere wijziging een veld dat wordt vergeten

SPEC-SEC-IDENTITY-ASSERT-001 Phase D (28 april) brak alle retrieval-callers omdat de SPEC één van de vier callers vergat. SPEC-SEC-CORS-001 vermeldt `klai-focus/research-api/app/main.py` in zijn CORS-lint — een service die niet draait. Elke security-SPEC die over interne callers gaat, moet research-api meenemen "voor de zekerheid", terwijl die zekerheid een fictie is.

### Verlaten user-data zonder retentie-pad

15 Qdrant-points en 2 PDFs van tenant `362757920133283846` liggen sinds 25 maart op disk. Er is geen UI om ze te benaderen, geen export-pad, geen retentie-job. GDPR-mismatch: data zonder houder.

---

## Goal

`research-api` als concept volledig uit het systeem verwijderen — zo dat geen enkele grep, allowlist of dashboard nog suggereert dat de service kan terugkeren. Tegelijk de bijbehorende user-data wissen volgens de keuze van SPEC-PORTAL-UNIFY-KB-001 D6 ("Focus data is not migrated").

Succes = na deze SPEC slaagt `rg "research-api|klai_focus|klai-focus" -g '!.moai/specs/**' -g '!CHANGELOG.md'` met 0 hits in actieve code. SERVERS.md zegt niet langer "up". `klai_focus` collection bestaat niet in Qdrant. `/opt/klai/research-uploads/` is leeg.

---

## Scope

### In scope

**Code (repo-zijde)**
- `klai-focus/` directory: `git rm -r`.
- `klai-retrieval-api/retrieval_api/services/search.py`: `_search_notebook`, `_notebook_filter`, `klai_focus`-doc-strings weg. `scope="notebook"` dispatch én `scope="broad"` parallel-merge-tak (regels 419-444) weg. De `broad`-tak wordt geen "alleen klai_knowledge"-versie — het is volledig onbruikbaar zonder Focus en heeft geen callers, dus de hele tak verdwijnt.
- `klai-retrieval-api/retrieval_api/api/retrieve.py` + `chat.py`: `scope=notebook` én `scope=broad` validation + branches weg. Conditionals als `if req.scope != "notebook"` (regels 177, 205, 246, 411) en `if req.scope != "notebook" and raw_results` worden vereenvoudigd nu beide takken weg zijn — en parallel `if req.scope != "broad"` checks (graphiti/link-expand/reranker skipping voor Focus) weg.
- `klai-retrieval-api/retrieval_api/models.py`: `Literal["personal","org","both","notebook","broad"]` → `Literal["personal","org","both"]`. `notebook_id` veld weg.
- `klai-retrieval-api/retrieval_api/config.py`: `qdrant_focus_collection` weg.
- `klai-retrieval-api/tests/`: `test_notebook_filter.py` weg, `test_broad_search_merges` (in `test_search.py`) weg, `test_search.py` / `test_api.py` / `test_assertion_mode_taxonomy.py` notebook-én-broad-paragrafen weg.
- `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:247`: `collections = ["klai_knowledge"]` (klai_focus eruit).
- `klai-portal/backend/app/api/proxy.py`: regel-10 comment over `proxy_research` weg (de handler bestaat al niet meer; comment is misleidend).
- `klai-libs/identity-assert/klai_identity_assert/models.py`: `research-api` uit `KNOWN_CALLER_SERVICES`. Comment over 2026-05-05 hotfix herschrijven naar het opruim-feit.
- `klai-libs/service-auth/klai_service_auth/scopes.py`: `svc-research-api` uit scope-grant docstring.
- `klai-libs/image-storage/klai_image_storage/url_guard.py`: `research-api` + `klai-focus` uit allowlist (regel 69, 80).
- `klai-libs/image-storage/tests/test_url_guard.py`: research-api regel weg (138).
- `klai-portal/backend/app/services/identity_verifier.py`: research-api uit copy-allowlist (regel 64) + comment-update.
- `klai-portal/backend/app/services/source_extractors/_url_validator.py`: regel 49 weg.
- `klai-portal/backend/tests/test_source_extractors_ssrf.py`: regel 205 weg.
- `klai-portal/backend/tests/test_deprovisioning_steps.py`: regels 378, 396 — assertions over `klai_focus` weg.
- `klai-knowledge-ingest/tests/test_url_validator.py`: regel 181 weg.
- `klai-portal/frontend/e2e/SPEC-PORTAL-UNIFY-KB-001.spec.ts`: blijft (test van de redirect, nog steeds geldig).
- `rules/cors_middleware_last.yml`: regel 80 (`klai-focus/research-api/app/main.py`) uit ast-grep config.
- `.github/workflows/semgrep.yml`: regels 10, 25, 61 — `klai-focus/**` paths weg.
- `deploy/caddy/Caddyfile`: regel 310 comment "research-api removed" weg (history is in SPEC, niet in Caddyfile).

**Documentatie**
- `docs/architecture/knowledge-ingest-flow.md` regels 799, 843, 991, 994 — `broad` rij/regel uit scope-tabel; tekst over "Focus notebook + org KB merge" weg of als historisch gemarkeerd.

**Infra-zijde (klai-infra repo)**
- `klai-infra/core-01/.env.sops`: SOPS decrypt → `KUMA_TOKEN_RESEARCH_API` + `RESEARCH_API_ZITADEL_AUDIENCE` weg → encrypt-in-place. Volgens `pitfalls/process-rules.md` → `sops-roundtrip-line-count-check` (verwacht delta = -2).
- `klai-infra/SERVERS.md`: regels 71, 126, 137, 228, 229 — research-api refs weg of als "decommissioned 2026-05" bijgewerkt.
- `klai-infra/INTERNAL_SECRET_ROTATION.md`: research-api regels weg (64, 143).

**Productie-state (eenmalige operator-actie, runbook)**
- `/opt/klai/research-uploads/` rm.
- `/opt/klai/research-api-src/` rm.
- Qdrant `klai_focus` collection: `DELETE /collections/klai_focus` via Qdrant API.
- `/opt/klai/Caddyfile` regel 207-212 comment-block opruimen (parallelle wijziging in repo Caddyfile).

**CI**
- `klai-focus/.github/workflows/research-api.yml` weg (komt mee met `git rm -r klai-focus/`).

### Out of scope (expliciet)

- ❌ Focus data migreren naar Knowledge — SPEC-PORTAL-UNIFY-KB-001 D6 heeft dit expliciet uitgesloten en de eigenaar (Mark) heeft op 2026-05-05 bevestigd: opruimen.
- ❌ Architectuur-wijziging aan retrieval-api buiten het verwijderen van scope=notebook (geen herinrichting van scope-dispatch, geen renaming).
- ❌ Alembic-migration in retrieval-api: research-api had zijn eigen DB; retrieval-api zelf gebruikt Postgres niet voor Focus-data.
- ❌ Per-tenant-notificatie aan tenant `362757920133283846`: data is `_focus`, sinds 2026-04-23 niet meer toegankelijk via UI; de redirect heeft de gebruikers al doorgestuurd.
- ❌ Notebook-functionaliteit als feature in `/app/knowledge` reanimeren.

---

## Requirements (EARS format)

### Ubiquitous

- **R-U1.** Het systeem bevat na deze SPEC geen actieve code-paden meer die `klai_focus` Qdrant-collection of de `research-api` service-naam vereisen.
- **R-U2.** SERVERS.md beschrijft geen `research-api` als levend onderdeel van de stack.
- **R-U3.** Geen CI-workflow build of deployt `research-api` images.

### Event-driven

- **R-E1.** Wanneer een caller `scope=notebook` of `scope=broad` naar retrieval-api `/retrieve` of `/chat` stuurt, antwoordt de service met HTTP 422 `unprocessable_entity` (Pydantic `Literal` rejection) — niet met een 400 `notebook_id required` of een silent merge-zonder-resultaten.
- **R-E2.** Wanneer portal-api een tenant deprovisioned, wordt alleen de `klai_knowledge` collection geraakt; `klai_focus` staat niet meer in de target-list.

### State-driven

- **R-S1.** Terwijl een ontwikkelaar `rg "research-api"` runt op de monorepo (excl. `.moai/specs/**` + `CHANGELOG.md`), is het resultaat 0 matches.
- **R-S2.** Terwijl een ontwikkelaar `rg "klai_focus|klai-focus"` runt (zelfde excludes), is het resultaat 0 matches.
- **R-S3.** Terwijl een ontwikkelaar `rg '"broad"' --include='*.py'` runt op `klai-retrieval-api/`, is het resultaat 0 matches in production-code (tests met `_is_broad_except` etc. zijn unrelated en blijven).

### Optional

- (geen)

### Unwanted

- **R-X1.** Tijdens de implementatie wordt geen nieuwe abstractie geïntroduceerd (geen "decommissioning service", geen feature flag, geen migratie-pad).
- **R-X2.** Geen wijziging buiten de in scope-lijst, ook niet "while we're at it"-cleanups in retrieval-api of portal-api.
- **R-X3.** De acceptance grep-gates kennen géén excludes naar `klai-focus/` — die directory bestaat niet meer na deze SPEC.

---

## Design Decisions

### D1: Eén PR, één worktree

Per `pitfalls/process-rules.md` → `worktree-for-long-running-changes` en `spec-work-in-a-worktree`: `git worktree add ../klai-decomm-focus -b feature/SPEC-DECOMM-FOCUS-001 main`. Alle code-wijzigingen daarbinnen. Eén PR naar main; touches >15 files over 6 services dus peer review verdient.

### D2: Server-side cleanup is een runbook, geen auto-deploy

De Qdrant collection-drop, `/opt/klai/research-uploads` wipe, en `/opt/klai/research-api-src` wipe zijn destructieve operaties op productie. Deze landen in `docs/runbooks/decommission-focus.md` met expliciete `ssh core-01` commando's; operator (Mark) voert handmatig uit na PR-merge. Geen GitHub Actions step die `docker exec ... DELETE collection` runt — te makkelijk per ongeluk te triggeren bij een revert.

### D3: SOPS-edit volgt het standaard roundtrip-pattern

Per `pitfalls/process-rules.md` → `sops-roundtrip-line-count-check`: ssh core-01, decrypt, sed-remove de twee regels, encrypt-in-place, verifieer `wc -l` delta = -2 ten opzichte van `/opt/klai/.env`. Geen GitHub-side editing — SOPS-edits gebeuren altijd op core-01.

### D4: Allowlist-removal vereist receiver-zijde test

Per `pitfalls/process-rules.md` → `retrieve-caller-service-header-mismatch`: bij elke wijziging aan `KNOWN_CALLER_SERVICES` MOET er een test zijn die het effect lockt. Voor deze SPEC: een nieuwe test `test_research_api_caller_rejected.py` in retrieval-api die `X-Caller-Service: research-api` POST en 400 `unknown_caller_service` verwacht. Bewijst dat removal effectief is, niet alleen cosmetisch.

### D5: Geen wijziging aan SPEC-PORTAL-UNIFY-KB-001

Die SPEC blijft in zijn huidige staat (`status: implemented`). Deze SPEC verwijst er expliciet naar als de oorspronkelijke decommission; deze SPEC is de afronding daarvan, niet een vervanging.

### D6: Retentie-event op deletie

`product_events` event `focus.legacy_data_purged` met `point_count: 15` + `pdf_count: 2` + `tenant_id: <id>`. Niet een complete audit-log, wel een spoor in product_events zodat compliance later kan zien dat data effectief gewist is.

---

## References

- **Aanverwante SPECs:**
  - `SPEC-PORTAL-UNIFY-KB-001` (status: implemented) — startte de decommission, liet residu's achter.
  - `SPEC-SEC-IDENTITY-ASSERT-001` (status: implemented) — Phase D + REQ-5 introduceerden de stale fixes op research-api.

- **Pitfalls die deze SPEC respecteert:**
  - `scale-the-answer-to-the-problem` — opruim-PR, geen architectuur.
  - `worktree-for-long-running-changes` + `spec-work-in-a-worktree` — toegewijde worktree.
  - `sops-roundtrip-line-count-check` — SOPS-edit op core-01.
  - `retrieve-caller-service-header-mismatch` — receiver-side test bij allowlist-wijziging.
  - `search-broadly-when-changing` — alle case-varianten checken in grep-gates.
  - `verify-image-pullable-before-pin` (n.v.t. — geen image-pin in deze SPEC).

- **Code-ankers:**
  - `klai-focus/` (te verwijderen, 41 files)
  - `klai-retrieval-api/retrieval_api/services/search.py` (`_search_notebook`)
  - `klai-libs/identity-assert/klai_identity_assert/models.py` (KNOWN_CALLER_SERVICES)
  - `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:247`
  - `klai-infra/core-01/.env.sops` (RESEARCH_API_*, KUMA_TOKEN_RESEARCH_API)
  - `klai-infra/SERVERS.md` (regels 71/126/137/228/229)
