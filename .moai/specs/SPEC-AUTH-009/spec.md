---
id: SPEC-AUTH-009
version: "2.0.0"
status: draft
created: "2026-04-30"
updated: "2026-04-30"
author: MoAI
priority: P1
supersedes: null
related: [SPEC-AUTH-006, SPEC-AUTH-008]
---

## HISTORY

| Date       | Version | Change                                                                                              |
|------------|---------|-----------------------------------------------------------------------------------------------------|
| 2026-04-30 | 1.0.0   | Initial draft — N:M domain allowlist + DNS verification + picker (overengineered, discarded)        |
| 2026-04-30 | 1.1.0   | Dropped legacy-grandfathering ceremony (no production users yet)                                    |
| 2026-04-30 | 2.0.0   | Full rewrite. Founder-implicit domain ownership. Allowlist + DNS verification deleted from scope. Admin handover added. Industry-standard model per Notion/Slack hybrid. |

# SPEC-AUTH-009: Multi-Tenant Workspace Discovery & Admin Handover

## Context

SPEC-AUTH-006 introduced an admin-managed allowlist (`portal_org_allowed_domains`) where a
workspace admin types in trusted email domains. Two problems with that model:

1. **Wrong claim mechanism.** Admins could allowlist any domain, regardless of whether they
   were actually from that domain. Phishing-shaped: an attacker with a `@voys.nl` mailadres
   could create a fake workspace and force themselves into legitimate Voys colleagues'
   pickers. The mitigation in SPEC-AUTH-006 (free-email blocklist + global domain
   uniqueness) only blunted the obvious vector.
2. **Wrong primary use case.** A B2B workspace is created by a real human signing up with
   a real company email. That signup IS the domain claim — Google/Microsoft already
   verified the user belongs to the domain at the IDP layer, and email-validation at
   signup proves the same for password-based signup. A separate admin UI to "add allowed
   domains" duplicates information that signup already established.

This SPEC replaces the entire allowlist mechanic with the implicit-founder model used by
Notion (for non-enterprise), Slack, and most consumer-tier B2B SaaS:

- The founder's verified email domain becomes the workspace's `primary_domain`.
- New users with a matching email domain see existing workspaces in a picker.
- Joining is a join-request by default; admins can flip an auto-accept toggle if they want.
- Admin handover (promote / demote / leave with min-1-admin enforcement) is added so a
  workspace remains recoverable when the founder leaves.

DNS-based domain verification is **explicitly out of scope** for v1. It is the recovery
mechanism for "wrong founder claimed our domain" and "founder left without handover" in
enterprise-grade products. Klai will tolerate those risks for now and can add DNS
verification as a separate enterprise SPEC when a paying customer requires it.

---

## Scope

| Layer       | Changes                                                                              |
|-------------|--------------------------------------------------------------------------------------|
| DB          | Add `primary_domain` + `auto_accept_same_domain` to `portal_orgs`. Drop `portal_org_allowed_domains` table. |
| Backend     | Modified `idp_callback` (domain-match → picker); workspace-create sets `primary_domain` from founder; admin handover endpoints; SPEC-AUTH-006 admin/domains endpoints removed |
| Frontend    | `/select-workspace` picker UI extended; new "Auto-accept" toggle in workspace settings; new admin-handover UI in users-admin; `/admin/domains` route deleted |
| i18n        | New strings for picker entry types, auto-accept toggle, admin handover |

## Out of Scope

- DNS-based domain verification (TXT challenge, ownership proof) — separate enterprise SPEC if/when needed
- Multi-domain per workspace (e.g. workspace accepts both `voys.nl` and `voys.com`) — same future SPEC
- Cross-workspace admin take-over by external claimant (Notion's "claim domain to override admin") — same future SPEC
- Personal `@gmail.com` workspaces (free-email blocklist applies to `primary_domain`)
- Legacy SPEC-AUTH-006 data migration ceremony — Klai is pre-launch, test data is wiped

---

## Requirements

### R1: Workspace `primary_domain` set at creation

**WHEN** a user creates a new workspace via self-serve signup (via SSO or via email +
validation),
**THEN** the workspace SHALL store the email domain of the creator's verified mailadres
in `portal_orgs.primary_domain`.

Schema:

```sql
ALTER TABLE portal_orgs
  ADD COLUMN primary_domain VARCHAR(253) NOT NULL,
  ADD COLUMN auto_accept_same_domain BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX ix_portal_orgs_primary_domain
  ON portal_orgs (primary_domain)
  WHERE deleted_at IS NULL;
```

**Constraints:**
- C1.1: `primary_domain` is normalised lowercase, whitespace stripped, before insert.
- C1.2: `primary_domain` is **immutable** after creation. No endpoint exposes UPDATE for it.
  If a customer ever needs to change it (rebrand, acquisition), Klai support handles it
  manually until a SPEC adds it.
- C1.3: A free-email domain (`gmail.com`, `outlook.com`, etc., per the existing blocklist
  in `app/services/domain_validation.py`) MUST be rejected at signup with HTTP 400. The
  user can still receive invitations to existing workspaces; they just cannot create one.
- C1.4: Multiple workspaces MAY share the same `primary_domain`. Voys can have a Voys
  workspace and a Pinger workspace, both with `primary_domain='voys.nl'`. The picker (R3)
  shows both.
- C1.5: The signup endpoint MUST verify the user's mailadres before creating the workspace.
  For SSO this is automatic (IDP provided the verified email); for password signup the
  validation-link click is required first.

### R2: Drop `portal_org_allowed_domains`

**WHEN** the SPEC-AUTH-009 Alembic revision runs,
**THEN** the table `portal_org_allowed_domains` and all its endpoints, frontend routes,
and i18n strings introduced by SPEC-AUTH-006 SHALL be removed.

**Constraints:**
- C2.1: Drop the table (`DROP TABLE portal_org_allowed_domains`). Klai is pre-launch; the
  existing rows are test data and will not be migrated.
- C2.2: Remove the FastAPI router file `app/api/admin/domains.py`.
- C2.3: Remove the model class `PortalOrgAllowedDomain` from `app/models/portal.py`.
- C2.4: Remove the frontend route `frontend/src/routes/admin/domains.tsx` and regenerate
  `routeTree.gen.ts`.
- C2.5: Remove the i18n keys `admin_domains_*` from `messages/en.json` and `messages/nl.json`
  (Paraglide regeneration follows automatically).
- C2.6: Remove the related tests under `klai-portal/backend/tests/` that exercise the old
  endpoints and model (`test_admin_domains.py`, `test_allowed_domains.py`,
  `test_domain_validation.py` — keep the parts of the last that test the free-email
  blocklist since R1-C1.3 still uses it).

### R3: Picker shows workspaces with matching primary_domain

**WHEN** a user authenticates (via SSO or via existing email+password) and the system
needs to decide where to send them,
**THEN** the backend SHALL find:

```python
member_orgs   = portal_users WHERE zitadel_user_id == user.zitadel_user_id
domain_orgs   = portal_orgs WHERE primary_domain == user.email_domain
                            AND deleted_at IS NULL
                            AND id NOT IN member_orgs.org_ids
```

And route the user as follows:

| Case                                                          | Action                                                                                                |
|---------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| `len(member_orgs) == 0 AND len(domain_orgs) == 0`             | Redirect to `/no-account` with a CTA to start a new workspace via self-serve signup                   |
| `len(member_orgs) == 1 AND len(domain_orgs) == 0`             | Finalize auth, set cookie, redirect to that workspace (existing single-membership flow, unchanged)    |
| `len(member_orgs) == 0 AND len(domain_orgs) == 1`             | Skip picker, redirect to `/select-workspace?ref=…` with a single domain_match entry (consistent UX)   |
| Any other combination (≥2 total entries)                      | Store entries in pending-session, redirect to `/select-workspace?ref=…`                               |

The single-domain-match case (third row) deliberately does NOT skip the picker — the user
must explicitly click "Word lid" or "Vraag toegang aan" because joining a workspace they
were not previously a member of is a real action that deserves an explicit choice.

**Constraints:**
- C3.1: Pending-session payload uses the existing `PendingSessionService` (Redis, 10-min TTL).
  The `entries` field replaces the old `org_ids: list[int]` shape:
  ```python
  class PendingEntry(TypedDict):
      org_id: int
      name: str               # display name (org.name)
      slug: str               # for tenant-subdomain construction
      kind: Literal["member", "domain_match"]
      auto_accept: bool       # only meaningful when kind == "domain_match"
  ```
- C3.2: Picker UI per `klai-portal-ui` patterns (AuthPageLayout, rounded-xl cards,
  sentence-case, no uppercase). Three card states by entry semantics:
  - `member` card: badge "Lid", subtitle "Lid sinds {date}"
  - `domain_match` + `auto_accept=true`: badge none, subtitle "Auto-toegang voor @{domain}",
    primary CTA on selection: "Word lid"
  - `domain_match` + `auto_accept=false`: badge none, subtitle "Vraag toegang aan via @{domain}",
    primary CTA on selection: "Vraag toegang aan"
- C3.3: Sort order: `member` entries first, then `domain_match` (auto_accept entries
  before request-only entries within domain_match). Alphabetical by `name` within each tier.
- C3.4: Footer link "Of begin een nieuwe werkruimte voor jouw bedrijf" links to
  `/$locale/signup?email={user_email}` (existing self-serve flow, email pre-filled).
  Always visible, regardless of how many cards are above it. Visually subordinate
  (`text-sm text-gray-400`) so the picker reads as a join-flow, not a re-signup-flow.
- C3.5: The `/no-account` page (single-zero case) ALSO offers the self-serve "begin een
  nieuwe werkruimte" CTA. The user is authenticated, has no path forward via existing
  workspaces, so creating their own is the natural next step.

### R4: Picker click → /api/auth/select-workspace branches by entry kind

**WHEN** the user selects an entry and clicks the primary CTA,
**THEN** `POST /api/auth/select-workspace` SHALL handle three branches based on the entry
type stored in the pending-session.

Request schema (existing endpoint, response extended):

```python
class SelectWorkspaceRequest(BaseModel):
    ref: str
    org_id: int

# Discriminated response
class SelectWorkspaceMember(BaseModel):
    kind: Literal["member"]
    workspace_url: str

class SelectWorkspaceAutoJoin(BaseModel):
    kind: Literal["auto_join"]
    workspace_url: str

class SelectWorkspacePending(BaseModel):
    kind: Literal["join_request_pending"]
    redirect_to: str    # /join-request/sent
```

**Constraints:**
- C4.1: The `org_id` in the request MUST appear in the pending-session's `entries` list.
  The backend MUST use the `kind` from the stored entry, NOT trust any client-supplied
  hint. Same enforcement pattern as old SPEC-AUTH-009 v1.0 R7-C7.1.
- C4.2: For `member` entries: same as today — `finalize_auth_request` + cookie + return
  workspace_url.
- C4.3: For `domain_match` + `auto_accept=true`: INSERT `portal_users` row with
  `role='member'`, `status='active'`. Send a notification email to all admins of the
  workspace via klai-mailer (template `auto_join_admin_notification_*`) — informational,
  not approval-needed. Then `finalize_auth_request` + cookie + return workspace_url.
- C4.4: For `domain_match` + `auto_accept=false`: create a `portal_join_requests` row
  (existing table from SPEC-AUTH-006), send the existing admin notification email
  (existing template `join_request_admin_*`), do NOT set the SSO cookie, return
  `redirect_to: '/join-request/sent'`.
- C4.5: The pending-session is consumed (deleted from Redis) in all three branches.
  Replay attempts return HTTP 410 Gone.
- C4.6: For `domain_match` + `auto_accept=true`, the INSERT must be idempotent: if the
  user already has a `portal_users` row in that org (race condition with a previous tab),
  catch IntegrityError, fall through to `finalize_auth_request` as if it were a `member`
  entry.
- C4.7: If `org_id` in the request is NOT present in the pending-session's `entries`
  list, return HTTP 403 Forbidden and DO NOT consume the pending-session. The user must
  retry from the picker with a valid org_id. This protects against tampered POSTs that
  reference orgs the user has no business joining.
- C4.8: If the pending-session is missing or expired (Redis returned None), return HTTP
  410 Gone with the i18n message `select_workspace_session_expired`. The frontend
  redirects to login.

### R5: Workspace setting — auto_accept_same_domain toggle

**WHEN** a workspace admin opens workspace settings,
**THEN** they SHALL see a toggle "Automatically accept users with @{primary_domain}" with
a clear explanation of consequences.

**Constraints:**
- C5.1: Endpoint `PATCH /api/admin/workspace` accepts `auto_accept_same_domain: bool`.
  Admin role required. Affects only the caller's org.
- C5.2: Default is `false`. When set to `true`, every future picker click on a
  `domain_match` entry for this workspace skips the join-request approval and inserts
  the user as member directly (R4-C4.3).
- C5.3: Existing pending join-requests are NOT auto-approved when the toggle is flipped
  to `true`. Only NEW signups benefit. Admin must approve the existing queue manually.
  Rationale: avoid silent bulk-approve that the admin did not consent to.
- C5.4: UI placement: workspace settings page (existing `/admin/settings` route or
  similar). Toggle visible only to admin role. Per `klai-portal-ui` patterns —
  sentence-case label, rounded-full toggle, helper text below explaining what happens.
- C5.5: When the toggle is `true`, the picker entry shows different microcopy and CTA
  (R3-C3.2) so the user knows they will join immediately, not wait for approval.

### R6: Admin handover — promote / demote / leave

**WHEN** a workspace admin needs to transfer control or leave the workspace,
**THEN** the system SHALL provide endpoints and UI to promote another member to admin,
demote oneself or another admin to member, and leave the workspace — with a hard rule
that every workspace MUST always have at least one active admin.

Endpoints (under existing `/api/admin/users`):

```
POST   /api/admin/users/{user_id}/promote-admin     -- promote member to admin
POST   /api/admin/users/{user_id}/demote-admin      -- demote admin to member
DELETE /api/admin/users/me                          -- leave workspace (self-removal)
```

**Constraints:**
- C6.1: `promote-admin`: only callable by an existing admin. Target must be active member
  in the same org. Sets `role='admin'`. No max-admin limit.
- C6.2: `demote-admin`: only callable by an existing admin. Target must currently be admin
  in the same org (otherwise HTTP 400 with detail "User is not an admin"). Sets
  `role='member'`. MUST refuse with HTTP 409 Conflict if this would leave the workspace
  with zero admins.
- C6.3: `DELETE /api/admin/users/me`: callable by any authenticated user. Removes the
  caller's `portal_users` row. If caller is admin AND would be the last admin, refuse with
  HTTP 409 Conflict and a message instructing to promote someone else first.
- C6.4: All three endpoints emit `product_events` for analytics:
  `user.role_promoted`, `user.role_demoted`, `user.left_workspace`.
- C6.5: Demoting yourself is allowed, leaving is allowed — both bounded by the
  min-1-admin rule, and on success the caller's session is invalidated (cookie cleared)
  if they removed themselves entirely.
- C6.6: Frontend: extend the existing `/admin/users` page. Each user row gets a
  "Make admin" / "Remove admin" action (admin-only visible). The current user's own row
  shows "Leave workspace" instead of "Remove admin". Use `InlineDeleteConfirm` or the
  existing confirm-dialog pattern for these actions per `klai-portal-ui`.
- C6.7: Edge case — workspace with one user (the founder) who tries to leave: refused with
  HTTP 409 with detail "Promote another admin or delete the workspace before leaving".
  Rationale: a workspace with no users is a zombie. The org-deletion flow itself is
  out-of-scope for this SPEC; if the existing codebase does not yet expose a
  delete-workspace endpoint, that gap is acknowledged and tracked separately. The
  implementation MUST verify whether such an endpoint exists during Phase 1 (research)
  and either reuse it or document the gap.
- C6.8: Notifications: when an admin is demoted by someone else, send them an in-app
  notification (or email — minor, decide at implementation time). Not a hard SPEC
  requirement.

### R7: Free-email enforcement on signup

**WHEN** a user attempts self-serve workspace creation via `/$locale/signup` with a
free-email domain (gmail, outlook, etc.),
**THEN** the signup endpoint SHALL refuse with HTTP 400 and a clear error message that the
user must use a company email or accept an invitation to an existing workspace.

**Constraints:**
- C7.1: Reuse the existing `is_free_email_provider()` from
  `app/services/domain_validation.py`. No new logic, just a different caller.
- C7.2: The error message in i18n: NL "Klai-werkruimtes kun je alleen aanmaken met een
  zakelijk mailadres. Vraag je beheerder om een uitnodiging als je via een privé-mailadres
  wilt deelnemen." — EN equivalent.
- C7.3: This check fires BEFORE the workspace row is created. The Zitadel user, if
  already created in a prior step, is left as-is — no rollback needed.

---

## Data flow

```
SSO login (idp_callback) — REWRITTEN
  └─ create_session_with_idp_intent → session
  └─ get_session_details → zitadel_user_id, email, email_domain
  └─ Concurrent lookup:
       • member_orgs    (portal_users WHERE zitadel_user_id = X)
       • domain_orgs    (portal_orgs WHERE primary_domain = email_domain
                                       AND id NOT IN member_orgs.org_ids
                                       AND deleted_at IS NULL)
  └─ Branch on totals:
       0 + 0  → /no-account?email=X  (with self-serve CTA)
       1 + 0  → finalize + cookie + workspace_url   (existing single-member flow)
       0 + 1  → /select-workspace?ref=…             (single domain_match, explicit click)
       any other → /select-workspace?ref=…          (multi-entry picker)

Picker click (POST /api/auth/select-workspace) — REWRITTEN
  └─ Validate ref + retrieve pending-session entry for org_id
  └─ Branch on entry.kind + entry.auto_accept:
       member                                  → finalize + cookie → { workspace_url }
       domain_match, auto_accept=true          → INSERT portal_users, notify admins,
                                                 finalize + cookie → { workspace_url }
       domain_match, auto_accept=false         → INSERT portal_join_requests, notify admins
                                                 → { redirect_to: '/join-request/sent' }
  └─ Always consume pending-session

Self-serve signup (existing /$locale/signup, modified)
  └─ Validate email is verified (SSO already done OR validation link clicked)
  └─ Reject if free-email domain
  └─ Create portal_orgs row with primary_domain = email_domain,
                                  auto_accept_same_domain = false
  └─ Create portal_users row for founder with role='admin'
  └─ Provision tenant infrastructure (existing flow)
  └─ Redirect to workspace

Admin handover
  └─ POST .../promote-admin  → row.role = 'admin'
  └─ POST .../demote-admin   → if last_admin → 409, else row.role = 'member'
  └─ DELETE .../users/me     → if last_admin → 409, else delete row + clear cookie
```

---

## Dependencies

| Dependency                              | Type     | Notes                                             |
|-----------------------------------------|----------|---------------------------------------------------|
| `portal_orgs` table                     | Existing | Add 2 columns; existing rows for test data have NOT NULL hole — handled by manual UPDATE in migration (test data only) |
| `portal_users` table                    | Existing | Unchanged                                         |
| `portal_join_requests` table            | Existing | Unchanged from SPEC-AUTH-006                       |
| `portal_org_allowed_domains` table      | Existing | DROPPED                                           |
| `pending_session` Redis service         | Existing | Payload schema extended (R3-C3.1)                 |
| `/select-workspace` route + page        | Existing | UI + behaviour redesigned per R3-R4               |
| `/api/auth/select-workspace`            | Existing | Response shape extended per R4                    |
| `/$locale/signup` self-serve page       | Existing | Modified to capture primary_domain + free-email block |
| `domain_validation.is_free_email_provider` | Existing | Reused in R7                                    |
| klai-mailer admin notification template | Existing | New template `auto_join_admin_notification_*` for R4-C4.3 |
| `/admin/users` page                     | Existing | Extended with promote/demote/leave actions (R6)   |

---

## Assumptions

- A-001: Klai is pre-launch. Existing test data in `portal_org_allowed_domains` and
  `portal_orgs.primary_domain=NULL` rows are wiped or hand-fixed by the engineer running
  the migration. No customer impact.
- A-002: Self-serve signup with SSO already proves the email is verified. For
  password+validation signup, the existing validation-link flow proves the same. No
  separate verification step is needed for the founder's domain claim.
- A-003: When a workspace is created, the founder's mailadres uniquely identifies them at
  Zitadel level. No need to re-verify at later logins; the Zitadel session is the source
  of truth for "is this still the same person".
- A-004: A workspace with `auto_accept_same_domain=true` accepts the trade-off that any
  newly-onboarded employee at the company instantly becomes a member without admin
  oversight. This is desirable for trusted-domain scenarios (small startups) and
  undesirable for security-conscious orgs (which keep the default `false`).
- A-005: Admin handover is intra-workspace only. Cross-workspace admin take-over (e.g.
  Klai Support manually transfers admin rights) is out of scope — handled via direct DB
  manipulation by the support engineer until a SPEC adds it.
- A-006: The picker single-domain-match case (R3 row 3) does not skip the picker. We
  prefer one consistent UX entry-point at the cost of one extra click in this specific
  case. Telemetry can later prove or disprove this.

---

## Risks

| Risk                                                                          | Impact | Mitigation                                                                                                          |
|-------------------------------------------------------------------------------|--------|---------------------------------------------------------------------------------------------------------------------|
| Wrong founder claims a domain (intern signs up first as "the Voys workspace")  | Medium | Accepted v1 risk. Real Voys CTO can: (a) request access via picker, (b) ask intern to promote them to admin and demote/remove the intern. R6 admin handover provides the recovery path. |
| Founder leaves company without handover                                        | Medium | R6 forces min-1-admin rule. If the lone admin truly disappears (deactivated mailbox, no Klai login), Klai support intervenes manually until enterprise SPEC adds DNS-based take-over. |
| Multiple workspaces with same primary_domain → user confusion in picker        | Low    | Sort + naming + "begin nieuwe werkruimte" footer. Same UX as Notion / Slack — confirmed scalable to 5+ entries.    |
| Auto-accept toggle abuse: admin enables it then forgets, every new signup auto-joins | Low    | Toggle is opt-in, default false. Admin sees notifications for every auto-join (R4-C4.3). Easy to flip back.        |
| Drop of `portal_org_allowed_domains` causes hidden test breakage                | Low    | Aggressive grep for the model name + endpoint paths in tests; CI runs full test suite.                              |
| `portal_orgs.primary_domain NOT NULL` migration fails on existing test rows    | Low    | Migration script does `UPDATE portal_orgs SET primary_domain = '<placeholder>'` for any row missing a value before the NOT NULL is enforced. Engineer reviews placeholders and either fixes or wipes by hand. |
| User races same auto_accept INSERT from two browser tabs                       | Low    | R4-C4.6: idempotent INSERT, IntegrityError → fall through to member flow.                                           |
| `auto_accept_same_domain=true` but workspace also has `primary_domain` blocked by free-email-list later | Very low | The blocklist applies at signup time only. A retroactive blocklist change does not affect existing workspaces. Decision is intentional. |

---

## Acceptance Criteria

### AC-1: Self-serve signup sets primary_domain

**Given** a user signs up at `/$locale/signup` with a verified `@bedrijf.nl` mailadres
**When** the workspace is created
**Then** `portal_orgs.primary_domain = 'bedrijf.nl'`
**And** the founder is added to `portal_users` with `role='admin'`
**And** `auto_accept_same_domain = false`

### AC-2: Free-email signup is rejected

**Given** a user attempts self-serve signup with `@gmail.com`
**When** the signup endpoint is called
**Then** the response is HTTP 400 with the i18n free-email error message
**And** no `portal_orgs` row is created

### AC-3: Same-domain picker shows existing workspaces

**Given** workspace A exists with `primary_domain='voys.nl'`
**And** user `lisa@voys.nl` has no `portal_users` rows
**When** Lisa signs in via Google SSO
**Then** the picker shows a `domain_match` entry for workspace A
**And** the footer "Of begin een nieuwe werkruimte" link is visible

### AC-4: Multi-workspace same-domain picker

**Given** workspace A (`primary_domain='voys.nl'`, auto_accept=false) and workspace B
(`primary_domain='voys.nl'`, auto_accept=true) both exist
**And** user `jan@voys.nl` is a member of A and not B
**When** Jan signs in via SSO
**Then** the picker shows two entries:
  - Workspace A: badge "Lid", subtitle "Lid sinds {date}"
  - Workspace B: subtitle "Auto-toegang voor @voys.nl", CTA "Word lid"

### AC-5: Auto-accept toggle skips approval

**Given** workspace A has `auto_accept_same_domain=true`
**And** user `nieuw@voys.nl` has no `portal_users` rows in A
**When** Nieuw selects A in the picker and clicks "Word lid"
**Then** a `portal_users` row is inserted with `role='member'`, `status='active'`
**And** the response is `{ kind: 'auto_join', workspace_url: '...' }`
**And** an admin notification email is sent to all admins of A

### AC-6: Default toggle keeps approval gate

**Given** workspace A has `auto_accept_same_domain=false` (default)
**And** user `extra@voys.nl` has no `portal_users` rows in A
**When** Extra selects A in the picker and clicks "Vraag toegang aan"
**Then** a `portal_join_requests` row is inserted
**And** the admin notification email is sent
**And** the response is `{ kind: 'join_request_pending', redirect_to: '/join-request/sent' }`
**And** no `portal_users` row is created until the admin approves

### AC-7: Picker entry-kind integrity (server-side)

**Given** the pending-session contains a `domain_match` entry with `auto_accept=false`
**When** a malicious request POSTs `{ ref, org_id }` where the client implies auto_accept
**Then** the backend uses ONLY the kind+auto_accept stored in the pending-session
**And** the request results in a join-request, NOT an auto-join

### AC-8: Promote / demote happy path

**Given** workspace A has admin X and member Y
**When** X calls `POST /api/admin/users/{Y.id}/promote-admin`
**Then** Y's role becomes admin
**And** subsequent `POST .../{X.id}/demote-admin` succeeds (Y remains as admin, so workspace still has one admin)
**And** X's role is now member

### AC-9: Last-admin demote refused

**Given** workspace A has exactly one admin X (and N members)
**When** X calls `POST /api/admin/users/{X.id}/demote-admin`
**Then** the response is HTTP 409 Conflict with detail "Workspace must have at least one admin"
**And** X's role remains admin

### AC-10: Last-admin leave refused

**Given** workspace A has exactly one admin X
**When** X calls `DELETE /api/admin/users/me`
**Then** the response is HTTP 409 Conflict with detail "Promote another admin before leaving"
**And** X remains in `portal_users`

### AC-11: Non-last-admin leave succeeds

**Given** workspace A has admins X and Y
**When** X calls `DELETE /api/admin/users/me`
**Then** X's `portal_users` row is deleted
**And** the SSO cookie is cleared in the response
**And** the response is HTTP 204 No Content

### AC-12: SPEC-AUTH-006 allowlist artefacts gone

**Given** the SPEC-AUTH-009 migration has run
**When** any HTTP client requests `/api/admin/domains` (any verb)
**Then** the response is HTTP 404
**And** the table `portal_org_allowed_domains` does not exist
**And** the model class `PortalOrgAllowedDomain` is not importable
**And** the route `/admin/domains` is not in `routeTree.gen.ts`

### AC-13: Picker has zero entries — graceful fallback

**Given** user `solo@bedrijf.nl` has no `portal_users` rows
**And** no workspace has `primary_domain='bedrijf.nl'`
**When** Solo signs in via SSO
**Then** they are redirected to `/no-account` (NOT the picker)
**And** the page shows a primary CTA "Begin een nieuwe werkruimte voor jouw bedrijf"
**And** clicking it navigates to `/$locale/signup?email=solo@bedrijf.nl` with email pre-filled

---

## Quality Gates

- [ ] `uv run ruff format .` + `uv run ruff check .` — no new errors
- [ ] `uv run --with pyright pyright` — no new errors
- [ ] `tsc --noEmit` — no new type errors
- [ ] `npm run lint` — no new errors
- [ ] Alembic migration is reversible: `alembic downgrade -1` then `alembic upgrade head`
      both succeed on a fresh schema
- [ ] All user-facing strings present in BOTH `messages/en.json` AND `messages/nl.json`;
      Paraglide regen committed
- [ ] `routeTree.gen.ts` regenerated: `/admin/domains` removed
- [ ] CI green: portal-api + portal-frontend + SAST/Semgrep
- [ ] Browser-verified: signup with `@bedrijf.nl` → workspace.primary_domain set correctly
- [ ] Browser-verified: free-email blocklist on signup (gmail.com refused with clear message)
- [ ] Browser-verified: same-domain second user → picker → join-request → admin email →
      approve → user is in
- [ ] Browser-verified: auto-accept toggle on → second user clicks Word lid → instant member
- [ ] Browser-verified: promote/demote/leave admin handover with min-1-admin enforcement
- [ ] Test: AC-7 explicit pen-test case (kind+auto_accept injection) in test_select_workspace.py
- [ ] Test: each of AC-9 / AC-10 has a dedicated test in test_admin_handover.py
- [ ] @MX:ANCHOR added to the new decision branch in `idp_callback` (high fan_in path)
- [ ] @MX:ANCHOR added to `select_workspace_endpoint` for the discriminated-response branch
- [ ] @MX:ANCHOR added to `demote_admin` and `leave_workspace` for the min-1-admin invariant
- [ ] Admin notification email template (`auto_join_admin_notification_*`) reviewed and
      added to klai-mailer
- [ ] Removed code: `app/api/admin/domains.py`, `app/models/portal.py::PortalOrgAllowedDomain`,
      tests for the dropped feature, frontend `routes/admin/domains.tsx`,
      i18n keys `admin_domains_*` — all confirmed absent post-merge
