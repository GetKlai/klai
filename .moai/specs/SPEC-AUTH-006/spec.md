---
id: SPEC-AUTH-006
version: "1.1.0"
status: draft
created: "2026-04-16"
updated: "2026-04-16"
author: MoAI
priority: P1
---

## HISTORY

| Date       | Version | Change                                                            |
|------------|---------|-------------------------------------------------------------------|
| 2026-04-16 | 1.0.0   | Initial SPEC creation                                             |
| 2026-04-16 | 1.1.0   | Add R9 (multi-org workspace selection); relax domain uniqueness   |
| 2026-04-16 | 1.2.0   | Fix three design issues: restore domain uniqueness for auto-join; fix join-request auth; replace URL token with Redis pending-session |

# SPEC-AUTH-006: SSO Self-Service — Domain Allowlist & Join Requests

## Context

Social login (Google, Microsoft) is live since SPEC-AUTH-006 precursor work (2026-04-16). When a
user authenticates via SSO but has no `portal_users` record, the portal sends them to the
`/provisioning` page which polls forever and never resolves. Two things are needed:

1. **Fase 2 — Domain allowlist**: Org admins configure trusted email domains
   (e.g. `bedrijf.nl`). Any SSO user whose email matches is automatically provisioned as a
   member of that org on first login — no invite required.

2. **Fase 3 — Join requests**: When SSO authentication succeeds but neither a direct invite
   nor a domain match exists, the user can submit a join request. The org admin receives an
   email with an approve/deny link.

A third prerequisite is out of scope for implementation here but is a dependency:

> **Fase 1 (prerequisite, implement first):** Detect "authenticated but no org" in the
> `callback.tsx` → `provisioning.tsx` flow and show a clear error page ("Je hebt geen Klai
> account. Vraag je beheerder om je uit te nodigen.") instead of the infinite spinner.
> This is a single frontend change with no backend work.

---

## Scope

| Layer          | Changes                                                          |
|----------------|------------------------------------------------------------------|
| DB             | Two new tables: `portal_org_allowed_domains`, `portal_join_requests` |
| Backend        | Auto-provision in `idp_callback`; new admin endpoints; klai-mailer extension |
| Frontend       | Admin domain settings page; join request page; admin request management |
| klai-mailer    | New `/internal/send` endpoint for platform-initiated transactional emails |

## Out of Scope

- SAML / enterprise SSO (Okta, Azure AD SSO at org level) — separate SPEC
- Automatic domain detection from the user's existing email at account creation
- Deprovisioning users when a domain is removed from the allowlist
- Rate-limiting join requests per email address (low priority, can add later)

---

## Requirements

### R1: Fase 1 — No-account error page (prerequisite)

**WHEN** a user completes SSO authentication AND `GET /api/me` returns `workspace_url: null`
AND `provisioning_status: "pending"` AND there is no `portal_users` row for this user,
**THEN** the portal SHALL display a clear error page instead of the provisioning spinner.

The page MUST communicate:
- Authentication succeeded (Zitadel user exists)
- No Klai workspace is linked to this account
- Actionable next step: contact the org admin or sign up

**Constraints:**
- C1.1: The error page must distinguish from a real provisioning job in progress. Use
  a new `me` field `org_found: bool` — `true` when a `portal_users` row exists, `false`
  when authenticated but unlinked.
- C1.2: The error page must not show the provisioning spinner at any point.
- C1.3: The Zitadel user created by the first SSO attempt remains in Zitadel. The
  backend does not delete it.

### R2: Domain allowlist — data model

**WHEN** an admin configures a trusted email domain for their org,
**THEN** the system SHALL persist it in `portal_org_allowed_domains`:

```sql
CREATE TABLE portal_org_allowed_domains (
    id          SERIAL PRIMARY KEY,
    org_id      INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    domain      VARCHAR(253) NOT NULL,  -- e.g. "bedrijf.nl"
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by  VARCHAR(64) NOT NULL,   -- zitadel_user_id of admin who added it
    UNIQUE (org_id, domain)
);
```

**Constraints:**
- C2.1: `domain` is stored lowercase, leading/trailing whitespace stripped.
- C2.2: A domain may be registered for at most ONE org (global unique constraint).
  If a domain is already claimed, the API returns 409 Conflict.
- C2.3: Only org admins (role = `admin`) may add or remove domains.
- C2.4: RLS policy: org members can read, org admins can insert/delete, others see nothing.

### R3: Domain allowlist — admin UI

**WHEN** an admin navigates to Settings → Toegang (or equivalent),
**THEN** they SHALL see a list of configured trusted domains with the ability to add or remove them.

**Constraints:**
- C3.1: Add form: single text field for the domain. Validation: valid domain format, not a
  free email provider (block `gmail.com`, `hotmail.com`, `outlook.com`, `yahoo.com`, etc.).
- C3.2: Remove: inline delete with confirmation (use `InlineDeleteConfirm` pattern).
- C3.3: If the domain is already claimed by another org, show: "Dit domein is al gekoppeld
  aan een andere organisatie."
- C3.4: Route: `/admin/settings/domains` (new route, separate from existing settings pages).

### R4: Domain allowlist — auto-provisioning on SSO login

**WHEN** `GET /api/auth/idp-callback` is called (SSO flow completes),
AND `create_session_with_idp_intent` succeeds,
AND no `portal_users` row exists for the resulting `zitadel_user_id`,
**THEN** the backend SHALL:

1. Fetch the session details from Zitadel to get the user's `zitadel_user_id` and `email`.
2. Extract the email domain (part after `@`, lowercased).
3. Query `portal_org_allowed_domains` for a row matching that domain.
4. If found: create a `portal_users` row with `role = "member"`, `status = "active"`.
5. Continue with the normal `finalize_auth_request` + cookie + redirect flow.
6. If not found: fall through to R5 (join request) or the no-account error page.

**Constraints:**
- C4.1: The Zitadel session details call is `GET /v2/sessions/{sessionId}` — the user ID
  is in `session.factors.user.id`.
- C4.2: Auto-provisioned users get `role = "member"`. Admins can promote later.
- C4.3: The provisioning is synchronous — no background job. The redirect must complete
  after the `portal_users` row is committed.
- C4.4: If the DB insert fails (e.g. race with another request), log the error and fall
  through to the join request flow rather than returning 500 to the user.
- C4.5: Auto-provisioning does NOT create a personal knowledge base. That remains an
  explicit invite-only step.

### R5: Join requests — data model

**WHEN** an SSO-authenticated user has no org and no domain match,
**THEN** the system SHALL allow them to submit a join request, persisted in `portal_join_requests`:

```sql
CREATE TABLE portal_join_requests (
    id              SERIAL PRIMARY KEY,
    zitadel_user_id VARCHAR(64) NOT NULL,
    email           VARCHAR(320) NOT NULL,
    display_name    VARCHAR(128),
    org_id          INTEGER REFERENCES portal_orgs(id) ON DELETE SET NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending',
       -- pending | approved | denied | expired
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     VARCHAR(64),   -- zitadel_user_id of admin who reviewed
    approval_token  VARCHAR(128) UNIQUE NOT NULL,
       -- HMAC-signed token for one-click email approval
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '7 days'
);
CREATE INDEX ON portal_join_requests (org_id, status);
CREATE INDEX ON portal_join_requests (zitadel_user_id);
```

**Constraints:**
- C5.1: `org_id` is derived from the email domain: if a domain match exists in
  `portal_org_allowed_domains` but auto-provisioning was disabled, use that org. If no
  domain match at all, `org_id = NULL` (platform-level request, reviewed by Klai team).
- C5.2: One pending request per `zitadel_user_id`. Duplicate submissions return 200
  (idempotent) without creating a second row.
- C5.3: `approval_token` is an HMAC-SHA256 of `(request_id + zitadel_user_id)` using the
  Fernet key. Tokens older than 7 days are expired automatically.
- C5.4: RLS: no public access. Only admin role can read/update for their org. Platform
  admin (internal service) can read/insert all.

### R6: Join request — user flow

**WHEN** SSO authentication succeeds but no org is found (R4 step 6),
**THEN** the portal SHALL redirect to `/join-request` and display a form:
- Pre-filled email (read-only, from Zitadel token)
- Editable display name
- Optional message to the admin (max 500 chars)
- Submit button

**WHEN** the user submits the form,
**THEN** the backend SHALL:
1. Create a `portal_join_requests` row.
2. Send a notification email to all admins of the matched org (or to the Klai platform
   address `access@getklai.com` if `org_id = NULL`).
3. Show the user a confirmation: "Je verzoek is ingediend. De beheerder van de werkruimte
   ontvangt een e-mail."

**Constraints:**
- C6.1: The join-request page is only reachable after SSO authentication — it reads
  the email from the OIDC token, not from user input.
- C6.2: If the user already has a pending request, show: "Er is al een verzoek ingediend
  op [datum]. Neem contact op met je beheerder als je nog geen reactie hebt ontvangen."
- C6.3: The submit endpoint is `POST /api/auth/join-request` (Bearer-authenticated —
  the user has a valid OIDC token after completing SSO). The email is taken exclusively
  from the verified OIDC token, never from the request body. Rate-limited: 3 requests
  per `zitadel_user_id` per day (not per IP, since the user is authenticated).

### R7: Join request — admin notification email

**WHEN** a join request is created,
**THEN** klai-mailer SHALL send an email to all active admins of the matched org
containing:
- Requester name and email
- Approve button (links to `POST /api/admin/join-requests/{id}/approve?token={approval_token}`)
- Deny button (links to `POST /api/admin/join-requests/{id}/deny?token={approval_token}`)
- Expiry date (7 days from request)

**Constraints:**
- C7.1: Portal-api calls klai-mailer via a new internal endpoint
  `POST /internal/send` (protected by `X-Internal-Secret` header, same secret as other
  internal endpoints). Payload: `{ template, to, locale, variables }`.
- C7.2: klai-mailer adds two new templates: `join_request_admin_nl` and `join_request_admin_en`.
- C7.3: If email delivery fails, the join request is still persisted. Log the error at
  `warn` level. The admin can still approve via the portal UI (R8).
- C7.4: Subject: `[Klai] Toegangsverzoek van {name} ({email})`.

### R8: Join request — admin management UI

**WHEN** an admin navigates to Settings → Toegangsverzoeken (or equivalent),
**THEN** they SHALL see a table of all pending join requests for their org with:
- Requester name and email
- Request date
- Optional message
- Approve / Deny action buttons

**WHEN** the admin approves a request,
**THEN** the backend SHALL:
1. Create a `portal_users` row (role = "member").
2. Mark the join request as `approved`.
3. Send the newly provisioned user a confirmation email ("Je hebt toegang tot de Klai
   werkruimte van {org_name}. Log in via {portal_url}") via klai-mailer.

**WHEN** the admin denies a request,
**THEN** the backend SHALL mark the request `denied` and optionally send a denial email.

**Constraints:**
- C8.1: Approve/deny via one-click email link (using `approval_token`) also works — no
  portal login required for the admin action if they have the token.
- C8.2: Route: `/admin/settings/join-requests`.
- C8.3: Approved users do NOT automatically get a personal KB (same as R4-C4.5).
- C8.4: After approval, the Zitadel user already exists — only `portal_users` is created.
  No new Zitadel invite is sent.

---

## Data flow

```
SSO login flow (idp_callback)
  └─ create_session_with_idp_intent → session
  └─ GET /v2/sessions/{id} → zitadel_user_id, email
  └─ portal_users lookup
       ├─ found → normal login ✓
       └─ not found
             └─ domain match in portal_org_allowed_domains?
                  ├─ yes → INSERT portal_users (auto-provision) → normal login ✓
                  └─ no  → redirect to /join-request
                              └─ POST /api/auth/join-request
                                   └─ INSERT portal_join_requests
                                   └─ notify admins via klai-mailer
                                   └─ show confirmation page

Admin approval flow
  ├─ via email link: GET /api/admin/join-requests/{id}/approve?token=...
  └─ via portal UI: POST /api/admin/join-requests/{id}/approve (Bearer auth)
       └─ INSERT portal_users → notify user → mark approved
```

---

## Dependencies

| Dependency                  | Type     | Notes                                             |
|-----------------------------|----------|---------------------------------------------------|
| `GET /v2/sessions/{id}`     | Zitadel  | Needed to resolve user ID + email from session    |
| `portal_org_allowed_domains`| New DB   | Alembic migration required                        |
| `portal_join_requests`      | New DB   | Alembic migration required                        |
| klai-mailer `/internal/send`| New API  | Shared `X-Internal-Secret` from existing config   |
| `portal_users` INSERT       | Existing | Reuse pattern from `admin/users.py` invite flow   |

---

## Assumptions

- A-001: A domain may only be claimed by one org globally for **auto-join**. This is
  the industry-standard approach (Notion, Linear both enforce 1:1 for domain auto-join).
  The workspace-picker (R9) handles users who are members of multiple orgs via invites —
  a separate and distinct case. The global `UNIQUE(domain)` constraint is restored.
- A-002: Free email provider blocklist is a static list in code, not configurable per org.
- A-003: The Klai-mailer `/internal/send` endpoint can reuse the existing
  `X-Internal-Secret` from `settings.internal_secret`.
- A-004: Auto-provisioned and join-request-approved users are treated identically to
  invited users after their `portal_users` row is created. No separate onboarding flow.

---

## Risks

| Risk                                                        | Impact | Mitigation                               |
|-------------------------------------------------------------|--------|------------------------------------------|
| `GET /v2/sessions/{id}` adds latency to every IDP callback  | Low    | Only called when no existing portal_user; cache is negligible since this is a one-time path |
| Domain squatting (attacker claims competitor's domain)       | High   | Manual review on add; C2.2 global unique; block free email providers |
| Approval token leakage via email forwarding                  | Medium | HMAC token scoped to request ID + user ID; 7-day expiry; single-use (mark used on first call) |
| Admin approves stale request (user already provisioned)      | Low    | Idempotent INSERT; unique constraint on (org_id, zitadel_user_id) prevents duplicate rows |
| klai-mailer `/internal/send` call fails silently            | Medium | C7.3: join request persisted regardless; admin sees it in portal UI |

---

### R9: Multi-org workspace selection

**Context:** A user may be a member of multiple Klai orgs — invited to each separately
(e.g. an employee who also has admin access to a second workspace, or a consultant working
across client accounts). Research shows Slack, Notion, and Linear all solve this with a
workspace-picker screen after authentication — the dominant B2B SaaS pattern. This is
distinct from the domain auto-join case: domains remain 1:1 (one domain → one org).
The multi-org case arises from **invitations**, not from multiple orgs claiming the same domain.

**WHEN** `idp_callback` resolves a `zitadel_user_id` that maps to `portal_users` rows in
**multiple** orgs (user was invited to several),
**THEN** the backend SHALL NOT redirect directly to a workspace. Instead it SHALL store a
pending-session record in Redis (TTL 10 min) keyed by a random UUID, containing
`{ zitadel_user_id, session_id, session_token, org_ids[] }`. The UUID is passed as a query
parameter to the frontend `/select-workspace?ref={uuid}` — no sensitive data in the URL.

**WHEN** the `/select-workspace` page renders,
**THEN** it SHALL display all eligible workspaces (org name, logo if available) and allow
the user to click one.

**WHEN** the user selects a workspace,
**THEN** the frontend calls `POST /api/auth/select-workspace` with `{ ref, org_id }`.
The backend retrieves the pending-session from Redis, validates `org_id` is in the allowed
list, sets the SSO cookie, deletes the Redis key (single-use), and returns the workspace URL.

**Constraints:**
- C9.1: Domain auto-join remains 1:1 (global `UNIQUE(domain)` constraint preserved in
  `portal_org_allowed_domains`). Multi-org selection only applies to users with multiple
  `portal_users` rows from invitations.
- C9.2: The pending-session is stored in Redis (existing Redis instance), not in a URL
  parameter. The URL contains only an opaque UUID. Session tokens never appear in URLs,
  logs, or referer headers.
- C9.3: If only one eligible org exists, skip the selection screen and go directly to
  that workspace (no unnecessary extra step for the common case).
- C9.4: The `/select-workspace` frontend route requires no Bearer token — the `ref` UUID
  IS the short-lived credential at that point.
- C9.5: Redis TTL: 10 minutes. Expired or missing ref → redirect to login.
- C9.6: Auto-provisioning (R4) provisions the user into the one matched org (domain is
  1:1). If after auto-provisioning the user is in multiple orgs, R9 applies.
- C9.7: The workspace-picker page is part of the portal frontend (not a separate service),
  served at `portal.getklai.com/select-workspace`.

**Note on phasing:** R9 can be implemented after R2-R4 without breaking the earlier work.
When only one org matches (the most common case today), the behaviour is identical to v1.0.
The selection screen activates only when `org_ids` contains more than one entry.

---

## Acceptance Criteria

### AC-1: Domain allowlist — happy path

**Given** an admin has added `bedrijf.nl` as a trusted domain for org A
**When** a new user authenticates via Google with email `jan@bedrijf.nl` for the first time
**Then** a `portal_users` row is created for that user in org A with `role = "member"`
**And** the user is redirected to their workspace without seeing the join request page
**And** a second login by `jan@bedrijf.nl` goes through the normal session flow (no duplicate insert)

### AC-2: Domain claim uniqueness

**Given** org A has claimed domain `bedrijf.nl`
**When** an admin of org B tries to add `bedrijf.nl`
**Then** the API returns 409 Conflict with detail "Dit domein is al gekoppeld aan een andere organisatie"

### AC-3: Free email provider blocked

**Given** an admin of any org
**When** they try to add `gmail.com` as a trusted domain
**Then** the API returns 400 with detail "Gratis e-mailproviders kunnen niet als vertrouwd domein worden toegevoegd"

### AC-4: Join request — new user, no domain match

**Given** no org has claimed domain `freelancer.nl`
**When** a new user authenticates via Google with email `jan@freelancer.nl`
**Then** they are redirected to `/join-request`
**And** submitting the form creates a `portal_join_requests` row
**And** admins of the closest matching org (or `access@getklai.com` if no match) receive an email

### AC-5: One-click approval from email

**Given** an admin receives a join request email with an approval link
**When** they click the approval link (no portal login required)
**Then** the `portal_users` row is created
**And** the join request is marked `approved`
**And** the requester receives a confirmation email

### AC-6: No duplicate join requests

**Given** user has a pending join request
**When** they submit the join request form again
**Then** the API returns 200 with the existing request's status (idempotent, no new row)

### AC-8: Multi-org workspace selection

**Given** user `jan@acme.nl` is a member of org A and org B (both claimed `acme.nl`)
**When** Jan authenticates via Google SSO
**Then** he sees the workspace-picker page listing both workspaces
**And** clicking org A redirects to `acme-a.getklai.com`

**Given** user `jan@acme.nl` is a member of only one org
**When** Jan authenticates via Google SSO
**Then** the workspace-picker page is skipped and Jan lands directly in his workspace

**Given** a workspace-selection token older than 10 minutes
**When** a user visits `/select-workspace?token=...`
**Then** they are redirected to the login page with an appropriate error

### AC-9: Fase 1 — no-account error page

**Given** an SSO-authenticated user has no `portal_users` row and no domain match
**When** the portal's `/callback` route resolves
**Then** the user sees a clear error page — NOT the provisioning spinner
**And** the page includes a call to action (contact admin or request access via R6)

---

## Quality Gates

- [ ] `uv run ruff format .` + `uv run ruff check .` — no new errors
- [ ] `tsc --noEmit` — no new type errors
- [ ] `npm run lint` — no new errors
- [ ] Alembic migration is reversible (`downgrade` tested locally)
- [ ] All user-facing strings in both `messages/en.json` and `messages/nl.json`
- [ ] Free email provider blocklist covers at minimum: gmail.com, hotmail.com, outlook.com, yahoo.com, live.com, icloud.com, proton.me, gmx.com
- [ ] CI green: portal-api + portal-frontend + SAST/Semgrep
- [ ] Browser-verified: full auto-provision flow (Google login → auto member → workspace)
- [ ] Browser-verified: full join request flow (no match → form → admin email → approve link → user access)
