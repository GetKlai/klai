# klai-portal backend

Inherits root `AGENTS.md` and `klai-portal/AGENTS.md`. This file holds the hard
gates that have caused production incidents. They are compact on purpose; the
full reference lives in `.claude/rules/klai/projects/portal-{backend,security}.md`
and `platform/zitadel.md` (Claude auto-loads these by path; under Codex, open
them when a gate below points you there).

## Before changing DB access, migrations, or auth code

RLS is live on portal tables (4-category framework). Read
`.claude/rules/klai/projects/portal-security.md` and `…/portal-backend.md`
before touching DB access or writing a migration.

## MUST — these have bitten production

1. **Alembic on Cat-A FORCE-RLS tables (`portal_users`, `portal_connectors`):
   no `UPDATE`/`INSERT` inside `upgrade()`.** WITH CHECK fires with no tenant
   context during migration → every row rejected → container crashloops. Pure
   DDL only in `upgrade()`; put per-row backfill in `post_deploy_<rev>.sql`
   (applied as the `klai` superuser). `ADD COLUMN … NOT NULL DEFAULT '…'` is
   safe (metadata-only); a later `UPDATE` backfill is not.
2. **New env var read by a pydantic validator → add it to
   `klai-infra/<server>/.env.sops` FIRST.** A validator that rejects empty
   values ships before the var exists = startup ValidationError = 502 crashloop.
   Env var first, validator second. Never the same deploy gap without the var.
3. **Encryption-key / secret fields need a `@field_validator(mode="after")`
   that rejects empty/invalid at startup.** Empty AES/Fernet key crashes
   mid-lifespan with a cryptic error; empty auth secret fails OPEN. Fail-closed,
   fail-loud, at module load.
4. **Compare secrets/tokens/signatures with `hmac.compare_digest`, never
   `==`/`!=`.** `==` short-circuits and leaks length/content via timing.
5. **Cat-A RLS tables are queried before tenant context is set** → use the
   inline NULLIF pattern, not the `_rls_current_org_id()` helper (helper raises
   42501 and 500s every authenticated request). Cat-D strict tenant tables use
   the helper. Never swap them.

Use CodeIndex `impact` before editing any shared helper. If the index is stale,
verify against source + git history — do not trust a stale call graph.

## Identity lifecycle gate (auth / invite / delete / offboard / suspend / IdP)

Klai identity spans TWO systems. A bug is only closed when BOTH are accounted
for, per state. Never reason about one in isolation. This is the gate that the
invite/password-reset incident bypassed by closing too early.

**Source of truth:** `sub` (OIDC subject) → `portal_users` → `portal_orgs`
join. `portal_users` is a mapping only (no email/name; identity is fetched live
from Zitadel). Never use `urn:zitadel:iam:user:resourceowner:id` — it is absent
from every Klai token (CI rule blocks reintroduction).

**Fill in the matrix before closing:**

| Dimension | States to check |
|---|---|
| portal_users row | exists / missing |
| portal_users.status | active · suspended · offboarded · invite_pending |
| Zitadel identity | exists / missing |
| Zitadel user state | active · initial · inactive(deactivated) · locked |
| Tenant membership | single vs multiple memberships |

**Delete semantics:** hard-delete the local row; remove the Zitadel identity
ONLY if this was the user's LAST membership; preserve it if the user still
belongs to another tenant.

**Re-invite semantics (where the bug lived):**
- `status=active` → resend invite; do not recreate the identity.
- `status=offboarded` → reactivation path, not a fresh create.
- **Legacy dangling Zitadel identity** (local row gone, Zitadel user still
  present) → must be detected and reconciled. A fresh invite against a dangling
  identity is what produced "Link has expired or is invalid". Always check this
  branch for delete-then-re-add scenarios.

**Evidence rule:** any claim about external cleanup MUST cite the exact Zitadel
API call (method + path) that performed or verified it. "Zitadel cleaned it up"
without the `DELETE /management/v1/users/<id>` cited is not done. Confirm a hard
delete actually removed the user; do not assume.

Background: `.claude/rules/klai/platform/zitadel.md`.
