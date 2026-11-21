# User Offboarding — Admin Workflow

> Commands are given as they run on core-01, from the deploy directory (the one holding `docker-compose.yml`). See [How public runbooks write commands](../contributing/infrastructure-boundary.md#how-public-runbooks-write-commands).

> Covers: SPEC-PORTAL-KB-OWNERSHIP-001 — admin disposition wizard for KB-owning
> users, hand-over of API keys, auto-revoke of MCP tokens, irreversible by design.
> Related runbook: `tenant-delete.md` (workspace-level delete, separate flow).

## When to use which lifecycle action

| Action | Effect | When to use |
|---|---|---|
| **Suspend** | Account locked, all data + memberships preserved. Reversible via Reactivate. | Temporary leave, password reset, suspected compromise pending investigation. |
| **Offboard** | Permanent. KBs transferred or purged per admin choice; API keys handed to an admin of your choice and keep working; MCP tokens revoked; Zitadel deactivated; GitHub org-membership removed. **Personal KBs are deleted immediately and cannot be restored.** | Permanent departure from the workspace. |
| **Reactivate** | Flips suspended → active. | Bring a previously-suspended user back. |

> **If you are not 100% sure the user is leaving permanently, suspend instead of
> offboard.** Suspend keeps every byte of their personal KB intact; offboard
> deletes the personal KB immediately and that KB is gone forever (per
> SPEC-PORTAL-KB-OWNERSHIP-001 owner decision D2 and D3).

## Happy path: admin offboards a user

Admin: navigate to `/admin/users`, find the user, click their row → Edit → scroll
to the lifecycle section → click **Offboard**.

### Step 1 — The wizard opens

The offboard wizard fetches `GET /api/admin/users/{id}/offboard-preview` and
renders three sections:

- **Nieuwe eigenaar van de API-keys** (only when the user created partner API
  keys) — the keys keep working and move to the admin picked here; the
  default is the offboarding admin, and only active admins are offered,
  because only admins can manage API keys. The person leaving may still know
  the keys, so replace a key when that matters.
- **MCP-tokens worden automatisch ingetrokken** (info banner) — number of
  active MCP tokens that will be revoked. No admin choice needed.
- **Team-kennisbanken (N)** — every org-owned KB the user is the SOLE owner of
  (creator + no other explicit owner-role grant). Each KB has a per-row
  disposition picker.
- **Persoonlijke kennisbanken (N)** — every personal KB the user owns. Locked
  to "Wordt verwijderd" — there is no transfer option per REQ-2.4.

### Step 2 — Choose a disposition per team-KB

For each team-KB the user solely owns:

- **Overdragen** (default) — pick a remaining active org-member as the new
  owner. The default is the offboarding admin themselves (Google Workspace
  "direct manager" pattern; SPEC owner decision D1). Click the receiver
  dropdown to override.
- **Verwijderen** — same 3-step purge as the regular delete-KB action
  (docs-app deprovision → knowledge-ingest delete → portal-DB delete).

### Step 3 — Submit

Click **Offboard**. The backend:

1. Validates that EVERY KB in the preview has a matching disposition. Missing
   slugs return 400 with `error_code: missing_kb_dispositions` and the explicit
   list — the wizard surfaces these inline.
2. Checks the new owner of the API keys (`api_keys_transfer_to`, default the
   acting admin): an active admin of this organisation who is not the person
   leaving, otherwise 400 with an `error_code` and nothing changes.
3. Applies the dispositions inside the offboard DB transaction.
4. Hands the API keys over (UPDATE `partner_api_keys` SET created_by =
   recipient WHERE created_by = target, after dropping their access rows on
   personal KBs) and soft-revokes MCP tokens (UPDATE `portal_mcp_tokens` SET
   revoked_at = NOW() WHERE user_id = target's portal_users.id AND revoked_at
   IS NULL).
5. Deletes group memberships (org-scoped, SEC-TENANT-001 invariant) and the
   cases the internal chat kept for the user's conversations
   (`internal_chat_cases`, also on a hard user delete).
6. Flips `portal_users.status = 'offboarded'`, emits `user.offboarded` audit.
7. Commits the transaction.
8. Post-commit: deactivates the user in Zitadel, removes them from the GitHub
   org if linked.

A successful offboard returns 200 with `{message: "User <id> offboarded."}` and
the wizard auto-navigates back to `/admin/users`.

### Step 4 — Verify

Audit-log entries to look for in the portal_audit_log (or VictoriaLogs
`service:portal-api` query):

| Action | When emitted |
|---|---|
| `kb.transferred` | Per org-KB transfer disposition. Details include `from_user`, `to_user`, `kb_slug`, `reason='offboarding'`. |
| `kb.admin_deleted` | Per org-KB delete disposition during offboarding. Details include `previous_owner`, `kb_slug`, `reason='offboarding'`. |
| `kb.personal_purged_on_offboard` | Per personal-KB purge. Details include `previous_owner`, `kb_slug`, `target_user_id`. |
| `user.offboarded` | One per offboard. Details include `kb_dispositions_count`, `api_keys_transferred`, `api_keys_transferred_to`, `mcp_tokens_revoked`. |

VictoriaLogs structlog mirrors carry the same field set under the same event
names (e.g. `event:kb_transferred`, `event:user_offboarded`).

## Failure modes

### "Missing dispositions for: [...]"
The wizard refused to submit because not every KB in the preview has a
disposition. This is REQ-2.5 — silent-orphans are the failure mode this SPEC
exists to prevent. The wizard renders the missing slugs inline; pick a
disposition and re-submit.

### "Personal knowledge bases cannot be transferred to another person"
The wizard normally hides the transfer option for personal KBs (lock-badge
"Wordt verwijderd"). If you somehow constructed a request that tries to
transfer a personal KB anyway (manual API call, browser devtools), the
backend refuses with 400 / REQ-2.4. Personal data stays personal — no
admin-pad to share a colleague's private KB.

### `transfer_recipient_not_member` / `transfer_recipient_not_active`
The receiving user is either not in your tenant, has been deleted, or has
status != active. Pick a different receiver from the dropdown — only
active+invite-accepted org-members appear as options. The same codes apply to
the new owner of the API keys.

### `api_keys_recipient_not_admin` / `api_keys_recipient_is_leaving_user`
The new owner of the API keys must be an admin, because only admins can
manage API keys, and cannot be the person who is leaving.

### Disposition transaction rolls back mid-offboard
Any failure inside `apply_dispositions` (docs-app down, knowledge-ingest
unreachable, etc.) raises and rolls back the entire offboard tx. The user
stays `active`. Status quo restored. Admin sees a 5xx; pick a different
disposition or retry once the upstream issue is fixed.

### Post-commit Zitadel deactivate fails
Same fail-open semantics as before this SPEC: `portal_users.status =
'offboarded'` is committed but the Zitadel side is still active. The user
can still log in until manually deactivated. This is the documented "DB-side
offboarded, IdP-side still active" state. Manual fix:

```bash
# As ops, on core-01
# Verify the gap:
docker exec klai-core-postgres-1 psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT zitadel_user_id, status FROM portal_users WHERE zitadel_user_id = '<id>';"
# Manually deactivate via Zitadel admin console or its management API.
```

A future SPEC could lift the deactivate into the same transaction (or run
post-commit retries), but it's not in scope today.

## Disposition decisions: when to transfer vs delete a team-KB

| Situation | Suggested disposition |
|---|---|
| KB still actively used, several team-members rely on its content | **Overdragen** to a remaining team-lead |
| KB was the user's hobby-project, no team-impact | **Verwijderen** — no owner orphans, no stale data |
| KB was a single-user experiment, never widely used | **Verwijderen** |
| KB content is canonical reference (process docs, policies) | **Overdragen** to the relevant department head |
| You are not sure | **Overdragen** to yourself first; you can always delete later via the regular delete-KB flow with the admin-override header |

The transfer is non-destructive — you can still delete the KB after
overdragen if it turns out nobody needed it. The reverse (delete-then-restore)
is impossible.

## Cancelling an in-progress offboard

There is no cancel-button after the offboard tx commits. Cancel options:

1. **Before clicking Offboard** — close the wizard. No DB writes have happened yet.
2. **During the API call** — close the tab. The wizard's mutation aborts; the
   API call completes server-side regardless. Outcome depends on where the
   request lands when the client disconnects (most likely fully applied).
3. **After commit** — irreversible. Per SPEC owner decision D3 there is no
   restore-on-rehire pad. To bring the user back you need to invite them
   fresh; their personal KB is gone.

## Suspend as the safer alternative

If you have any doubt, click **Suspend** instead. This:

- Locks the user's login (deactivates their portal session)
- Preserves all data: personal KB, org KBs they own, group memberships, API
  keys, MCP tokens
- Is fully reversible via Reactivate

Use suspend for: temporary leave (vacation, sabbatical), security incidents,
or "I think they're leaving but they haven't given notice yet". Convert
suspend → offboard later when the departure is confirmed and you've decided
the KB dispositions.

## Related SPECs

- SPEC-PORTAL-KB-OWNERSHIP-001 — this work (admin-delete + offboarding-transfer + personal-firewall)
- SPEC-INFRA-TENANT-DELETE-001 — workspace-level delete (not user-level)
- SPEC-PORTAL-RBAC-REFACTOR-001 — defines the ProfileRole.ADMIN gate this flow uses
- SPEC-SEC-TENANT-001 — tenant-scoped membership delete inside offboard
