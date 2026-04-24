# Secrets Matrix

Per-service env-var inventory for `deploy/docker-compose.yml`.
Required by [SPEC-SEC-ENVFILE-SCOPE-001](../.moai/specs/SPEC-SEC-ENVFILE-SCOPE-001/spec.md)
REQ-2 — the authoritative source of truth for "which service
legitimately reads which secret". Keep in lock-step with
`deploy/docker-compose.yml`: any PR that adds an env var to a service's
`environment:` block MUST add the matching row here.

Non-secret values (URLs to sibling services, feature flags, log levels,
model names, timeouts) are NOT listed — this matrix covers only
credentials, keys, tokens, and passwords. The rule of thumb: if the
value is in `/opt/klai/.env` / a SOPS file or looks like a secret, it
belongs here.

## Migration status

Services previously using `env_file: .env` (the shared global file at
`/opt/klai/.env`):

| Service | Migrated to explicit `environment:` | Commit |
|---|---|---|
| scribe-api | YES | (this commit) |
| retrieval-api | pending | — |
| victorialogs | pending | — |
| portal-api | pending | — |

Services using an acceptable per-service `env_file: ./<svc>/.env`
pattern (REQ-6 — compliant as-is, content audit is a follow-up):
klai-mailer, klai-connector, librechat-getklai.

## Matrix

Rows sorted by secret name, then by service.

| Secret | Service | Source | Purpose |
|---|---|---|---|
| `KNOWLEDGE_INGEST_SECRET` | scribe-api | `/opt/klai/.env` (SOPS `core-01/.env.sops`) | HMAC auth when scribe pushes transcripts to knowledge-ingest. |
| `LITELLM_MASTER_KEY` | scribe-api | `/opt/klai/.env` (SOPS `core-01/.env.sops`) | Bearer token for the LiteLLM gateway (AI summarization of transcripts). |
| `POSTGRES_PASSWORD` | scribe-api | `/opt/klai/.env` (SOPS `core-01/.env.sops`) | PostgreSQL password; interpolated into `POSTGRES_DSN` for the scribe schema. |

## Rotation coupling

See [SPEC-SEC-005](../.moai/specs/SPEC-SEC-005/spec.md) and
`klai-infra/INTERNAL_SECRET_ROTATION.md` — narrower per-service scope
means a secret rotation now touches only the services listed in its
column of this table. When this matrix changes, cross-check the
rotation runbooks.

## How to update this file

1. When a PR adds a new key to a service's `environment:` block, add a
   row here in the same PR. Reviewer rejects PR without the row.
2. When a PR removes a key from a service, remove the matching row.
3. Keep the table sorted (alphabetical by secret name, then service).
4. Use `/opt/klai/.env (SOPS core-01/.env.sops)` as the source for
   shared secrets. Per-service SOPS paths (e.g.
   `klai-infra/core-01/klai-mailer/.env.sops`) cite the per-service
   file.
