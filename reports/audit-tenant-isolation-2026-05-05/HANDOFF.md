# Handoff — laptop transfer 2026-05-06 ochtend

## Hoe je dit op je laptop ophaalt

```bash
git fetch --all
git checkout docs/audit-ti-2026-05-05    # this branch — alle audit-docs + SPECs
# Of een feature-branch reviewen:
git fetch origin feature/SPEC-TI-002-RLS-CONNECTOR
gh pr view 375 --web
```

Je hoeft GEEN worktrees te recreëren — de PRs (#375, #376, #377, #378, #379, #380, #381, #382, #383) staan al op origin.

## Wat is veranderd sinds RESULTS.md (laatste update gisterenavond)

### 1. ✅ #373 IS GEMERGED naar main

CRIT C-2 (`/api/admin/orgs/{slug}/retry-provisioning` platform-admin gate) is op main. Commit `7ca6a71c`.

### 2. 🚫 #374 GESLOTEN als moot

Ontdekt: `e6fabc73 feat: SPEC-DECOMM-FOCUS-001 — drop Klai Focus / research-api (#368)` is gemerged op main. Het hele klai-focus / research-api wordt gedecommissioneerd. Findings A-10, A-11, A-12 zijn moot.

### 3. ⚠️ Agent-isolatie was niet 100%

Ik ontdekte dat batch-3 agents (010A, 010B, 010C) niet volledig in worktree-isolation draaiden. Resultaat:
- Branch `feature/SPEC-TI-010B-REDIS` bevat commits van SPEC-TI-002 EN SPEC-TI-010B EN SPEC-TI-010C EN een revert van 010C. Die branch is "vervuild".
- PR #383 conflict-state komt hier vandaan.

Practical impact: de PRs op origin (#381, #382, #383) zijn niet schoon mergebaar. Need-cleanup.

## Status per PR (peildatum 2026-05-06 ~7:30 CEST)

| # | Status | Actie |
|---|---|---|
| ~~#373~~ | ✅ MERGED | klaar |
| ~~#374~~ | 🚫 CLOSED (moot, focus-decomm) | klaar |
| #375 | ✅ CI green | merge → post-deploy SQL |
| #376 | ⚠️ quality green, build-push fail | check Dockerfile context voor klai-libs path-deps |
| #377 | ✅ CI green | merge → post-deploy SQL |
| #378 | ✅ CI green | merge (geen post-deploy SQL nodig) |
| #379 | ✅ CI green | merge + `garage bucket website --deny klai-images` |
| #380 | ⚠️ quality green, build-push fail | **PRE-FLIGHT**: GITEA_WEBHOOK_SECRET in SOPS, dan zelfde Dockerfile-fix als #376 |
| #381 | ⚠️ quality fail (test-mocks niet bijgewerkt) + branch vervuild | herstart vanuit clean main |
| #382 | ⚠️ quality fail (3 test-fixtures niet bijgewerkt) | herstart vanuit clean main |
| #383 | ⚠️ MERGE CONFLICT + branch vervuild | herstart vanuit clean main |

## Aanbevolen volgorde op laptop

### Stap A: merge de 4 schone PRs (10 min werk)

```bash
# Pre-flight: voor #379 stop Garage website-mode
ssh core-01 "docker exec klai-core-garage-1 garage bucket website --deny klai-images"

# Merge in volgorde (admin-merge omdat branches BEHIND main raken):
gh pr merge 375 --admin --squash --delete-branch
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-connector/alembic/versions/post_deploy_008_rls_tenant_isolation.sql
ssh core-01 "docker restart klai-core-klai-connector-1"

gh pr merge 377 --admin --squash --delete-branch
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-portal/backend/alembic/versions/post_deploy_ti005_tenant_isolation_hygiene.sql
ssh core-01 "docker restart klai-core-portal-api-1"

gh pr merge 378 --admin --squash --delete-branch  # geen post-deploy

gh pr merge 379 --admin --squash --delete-branch  # garage already configured
```

Na deze 4 merges: 8 findings dicht (A-7, A-1..A-6, B-1, B-4) bovenop C-2 die al gemerged is.

### Stap B: build-push fix op #376 + #380

Beide hebben `quality: pass` maar `build-push: fail`. Inspecteer met `gh run view <run-id> --log-failed` om de Dockerfile-error te zien. Vermoedelijke cause: nieuwe `klai-libs/webhook-replay` path-dep maar Dockerfile heeft geen `COPY ../klai-libs/webhook-replay/`. Fix in beide:
- `klai-portal/backend/Dockerfile`: voeg COPY toe voor klai-libs/webhook-replay
- `klai-knowledge-ingest/Dockerfile`: idem
- Check of build context = repo-root (`context: .` in workflow), niet de service-dir

### Stap C: #381, #382 hervatten vanuit clean main

Branches zijn vervuild. Beste actie: nieuwe branches aanmaken vanuit current main (waar #373 al op staat) en de fixes opnieuw cherry-picken of opnieuw schrijven. De SPEC-files in `.moai/specs/SPEC-TI-010-CLEANUP-BATCH/` blijven geldig — gebruik die als referentie.

```bash
# Voor #381 (Redis hygiene):
git checkout -b fix/SPEC-TI-010B-redo main
git cherry-pick 81ae122a  # The SPEC-TI-010B commit from the polluted branch
# Then fix the test mocks in tests/test_app_templates.py:
#   Mock signature: invalidate_templates(zitadel_org_id="100000000000000001", ...)
#   not invalidate_templates(42, ...)
```

### Stap D: #383 schrijf opnieuw of laat liggen

Findings B-6 (feature_knowledge), B-7 (delete_by_source — alleen scribe deel relevant nu klai-focus weg is), B-8 (knowledge-ingest stats), C-11 (token-rate-limit). Allemaal MED+LOW, geen blocker voor livegang.

## Wat ik HEB gedaan vanochtend (laatste 18 min)

1. Ontdekt dat main 3 commits ahead is, waaronder DECOMM-FOCUS
2. Geconstateerd dat branch-pollution ervoor zorgt dat #381/#382/#383 niet schoon zijn
3. #373 admin-merged → CRIT op main
4. #374 closed als moot
5. docs/audit-ti-2026-05-05 branch op origin gepushed met alle reports + SPECs
6. Deze HANDOFF.md geschreven

## Wat ik NIET heb gedaan (tijd op)

- Build-push fixes op #376/#380 (Dockerfile context onbekend zonder verdere inspectie)
- Test-fixes op #381/#382 (branch-pollution maakt het complex)
- Rebase #383 (idem)
- research-api CI workflow toevoegen (moot — service is decomm)
- Deploy-script automation

## Belangrijkste les voor volgende keer

**Worktree-isolation bij parallelle Agent-spawn was niet waterdicht in deze sessie.** Drie batch-3 agents hebben elkaars commits op één branch geland. Volgende keer expliciet `Agent(isolation: "worktree")` opgeven (niet vertrouwen dat agent zelf `git worktree add` doet) of een serial workflow voor dezelfde-service edits.

🤖 Eindstand 2026-05-06 ~07:50 CEST
