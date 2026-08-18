---
paths:
  - ".claude/rules/klai/pitfalls/process-rules.md"
---
# Historical pitfall compatibility index

This file is intentionally scoped to itself. Current invariants live with the
code they govern; old incident reports and source comments may still use these
aliases:

| Historical name or theme | Maintained source |
|---|---|
| `validator-env-parity` | `infra/secrets.md`, `infra/deploy.md`, and the `env-scope-guard` skill |
| `alembic-stamped-past-skipped-migration`, multi-heads, ownership | `infra/deploy.md` and `projects/portal-security.md` |
| `grafana-uid-40-char-limit` | `infra/observability.md` and `scripts/audit-alert-uid-length.sh` |
| `retrieve-caller-service-header-mismatch` | `projects/knowledge.md` |
| bind-mount sync, restart versus recreate, `docker cp` | `infra/deploy.md` |
| callback host-class allowlisting | `projects/portal-backend.md` |
| template injection | `projects/mailer.md` |
| ast-grep rule discovery | `lang/ast-grep.md` |
| uv path/source dependencies | `lang/uv-dependencies.md` |
| previous red deploy workflow | `deploy-compose` skill |
| claim emission versus consumption | `platform/zitadel.md` |
| container cleanup and deletion safety | `infra/container-hygiene.md` |
| configured-but-never-wired, accepted-but-unused settings | `lang/testing.md` and `projects/knowledge.md` |
| queue-is-not-a-rate-limiter | `projects/knowledge.md` |
| remove-the-mount-before-you-delete-the-file, one-container-one-owner | `infra/deploy.md` and `infra/container-hygiene.md` |

Use the maintained rule, cited implementation, and regression test. Do not
restore narrative incident history to active agent context.
