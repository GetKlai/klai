# SPEC-TI-007 — Gitea webhook fail-closed + tenant-spoof fix

**Audit ref:** finding **C-1**
**Standards ref:** `standards.md` sections 5, 15
**Priority:** HIGH
**Status:** Ready

## Goal

Eliminate de twee compounding defects op `/ingest/v1/webhook/gitea`:
1. HMAC fail-open op leeg secret
2. Tenant-spoof via Gitea-org `description` field

## Acceptance criteria (EARS)

### Fail-closed (deel 1)
- **AC-1** `@field_validator("gitea_webhook_secret", mode="after")` in `klai-knowledge-ingest/knowledge_ingest/config.py` reject empty/whitespace, mirror van `_require_moneybird_webhook_token`.
- **AC-2** Pre-flight: `GITEA_WEBHOOK_SECRET` exists in `klai-infra/core-01/.env.sops` BEFORE merging (per `validator-env-parity` pitfall).
- **AC-3** Verwijder `if settings.gitea_webhook_secret:` wrapper rond HMAC-check op `routes/ingest.py:630`.

### Tenant-spoof fix (deel 2)
- **AC-4** Nieuwe tabel `knowledge.gitea_repo_to_org`:
  ```
  CREATE TABLE knowledge.gitea_repo_to_org (
      full_name TEXT PRIMARY KEY,
      org_id TEXT NOT NULL,
      created_at TIMESTAMPTZ DEFAULT now()
  );
  ```
  + RLS Cat-D policy.
- **AC-5** `_get_org_id` vervangt Gitea-API `description` lookup door SELECT op `knowledge.gitea_repo_to_org`.
- **AC-6** Portal-api admin endpoint `POST /api/admin/gitea-mappings` (platform-admin gated) om mappings te beheren — of automatisch geïnsert bij Gitea-connector creation in portal-api.

### Tests
- **AC-7** `test_gitea_webhook_secret_validator.py`: empty/whitespace → ValidationError.
- **AC-8** `test_gitea_webhook_tenant_resolution.py`: gespoofde Gitea-description → 404 (geen mapping); legitieme repo → 200.

## Implementation

1. Migration + post_deploy SQL voor nieuwe tabel.
2. Validator op config.
3. `routes/ingest.py` refactor: `_get_org_id` lookup van DB ipv Gitea-API.
4. Portal-api admin endpoint OF auto-insert bij connector-creation.

## Operator-step

```bash
# 1. Verify SOPS:
ssh core-01 "grep '^GITEA_WEBHOOK_SECRET=' /opt/klai/.env"  # MUST return non-empty

# 2. Apply migration:
ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < klai-knowledge-ingest/alembic/versions/post_deploy_<rev>.sql

# 3. Bootstrap mappings voor bestaande Gitea-connectors:
ssh core-01 "docker exec klai-core-portal-api-1 python -c '...bootstrap script...'"
```

## Worktree

`klai-gitea-harden` — `feature/SPEC-TI-007-GITEA-HARDEN`.
