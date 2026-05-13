# SPEC-TI-003 — RLS rollout op knowledge schema + identity-assertion op ingest endpoints

**Audit ref:** `reports/audit-tenant-isolation-2026-05-05/report.md` findings **A-8** + **A-13**
**Standards ref:** `standards.md` sections 1, 3, 7, 8, 9, 11
**Priority:** HIGH
**Status:** Ready

## Goal

Twee gecombineerde fixes voor de grootste data-bearing surface in klai:
1. **A-8:** ENABLE RLS op alle 9 + 4 junction tabellen in `knowledge.*` schema.
2. **A-13:** Identity-assertion op alle ingest endpoints die `org_id` uit body/query nemen — body-trust vervangen door cryptografische binding.

## Acceptance criteria (EARS)

### RLS (A-8)
- **AC-1** Helper-function `_rls_current_org_id() RETURNS text` (org_id in dit schema is text) in post_deploy SQL.
- **AC-2** ENABLE + FORCE + Cat-D policy `tenant_isolation` op: `artifacts`, `entities`, `crawl_domains`, `crawl_jobs`, `crawled_pages`, `kb_config`, `org_config`, `page_links`, `parent_chunks`.
- **AC-3** Junction-tabellen (`artifact_entities`, `artifact_images`, `derivations`, `embedding_queue`) krijgen subquery-policy via parent.
- **AC-4** `rag_eval_results` blijft RLS-vrij (analytics/eval, geen tenant-data).
- **AC-5** `entrypoint.sh` toegevoegd aan `klai-knowledge-ingest` voor auto-`alembic upgrade head` (vandaag mist die — `alembic-stamped-past-skipped-migration` pitfall).

### Identity-assertion (A-13)
- **AC-6** Receiver-side: `klai_identity_assert.IdentityAsserter` adopted op alle endpoints in `routes/knowledge.py`, `routes/crawl.py`, `routes/ingest.py`, `routes/stats.py`, `routes/internal.py` die `org_id` uit body/query lezen.
- **AC-7** Sender-side: alle portal-api callers (en andere services) die knowledge-ingest aanroepen sturen `X-Caller-Service` header. Mirror SPEC-SEC-IDENTITY-ASSERT-001 patroon.
- **AC-8** `INTERNAL_SECRET` blijft als netwerk-auth, identity-assertion komt erbij als tenant-auth.
- **AC-9** Procrastinate tasks (`crawl_tasks.py`, `enrichment_tasks.py`, `rebuild_tasks.py`) wrappen DB-werk in `tenant_scoped_session(org_id)` of equivalent.

### Tests
- **AC-10** `test_knowledge_rls.py`: fail-loud op missing tenant context, filter werkt, INSERT-bypass blokkeerd.
- **AC-11** `test_ingest_endpoints_identity_assertion.py`: claim-mismatch → 403; geen header → 403; correct claim → 200.
- **AC-12** Caller-side regressie: portal-api → knowledge-ingest http calls hebben `X-Caller-Service: portal-api`.

## Implementation

1. **DB-layer:** nieuwe alembic migration + post_deploy SQL met helper + ENABLE/FORCE/policy op alle tabellen.
2. **Sessie-helpers:** kopieer pattern naar `klai-knowledge-ingest/knowledge_ingest/db.py` (asyncpg-pool variant — Postgres-laag GUC via `set_config`).
3. **Auto-migrate:** `entrypoint.sh` script + Dockerfile CMD wijziging.
4. **Identity-assertion:** verwijder `org_id` velden uit request bodies waar mogelijk, derive uit `IdentityAsserter.verify(...)` result.
5. **Sender-side:** update `app/services/knowledge_ingest_client.py` (en analoge clients) met `X-Caller-Service: portal-api` header.

## Operator-step

```bash
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-knowledge-ingest/alembic/versions/post_deploy_<rev>.sql
docker restart klai-core-klai-knowledge-ingest-1
```

## Worktree

`klai-knowledge-rls` — `feature/SPEC-TI-003-RLS-KNOWLEDGE`.
