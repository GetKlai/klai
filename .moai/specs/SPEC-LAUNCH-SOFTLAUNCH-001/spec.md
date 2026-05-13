# SPEC-LAUNCH-SOFTLAUNCH-001 — Softlaunch Readiness

**Status:** Draft
**Type:** Audit + Gap-fix
**Created:** 2026-05-11
**Scope:** Block the door on first-user-facing defects in the
acquisition → activation → first-value path. Strategy / launch-sequence
lives in a separate (future) SPEC.

## Context

Klai has a public website (`getklai.com`) with a waitlist modal, and a
portal (`my.getklai.com`) with a working signup + provisioning flow.
The two are not connected: waitlist entries land in Twenty CRM as
`Waitlist – <Company>` deals in stage `NEW`. There is no automated
bridge to either an invitation email or a signup-token.

A 30-minute live + code audit (2026-05-11) found one hard blocker, one
likely blocker, three should-fix-before-softlaunch items, and a handful
of nice-to-haves. This SPEC enumerates them with severity, current
state, and acceptance criteria.

## Findings

### B-1 (Blocker) Signup password copy ↔ backend mismatch

**Current state.** `my.getklai.com/en/signup` shows the helper text
"Minimum 8 characters" (frontend hint). The backend enforces:

- ≥12 characters (length floor, REQ-22.2 SPEC-SEC-HYGIENE-001)
- zxcvbn score ≥3, with PII (email/name/company) factored in (REQ-22.1)

Any user who picks an 8–11 character password (or a weak 12+) gets a
generic 400 with detail
`"Wachtwoord is te zwak. Kies een langer of minder voorspelbaar wachtwoord."`
After typing the form correctly. First-impression damage.

**File:** `klai-portal/frontend/src/routes/$locale/signup/index.tsx`
(form hint), `klai-portal/backend/app/api/signup.py:107-117` (real
rules).

**Acceptance criteria.**
- WHEN the signup form renders, THE SYSTEM SHALL show
  "Minimaal 12 tekens, kies iets dat een gokwoorden-attack niet
  raadt." (NL) / "At least 12 characters, hard to guess." (EN).
- WHEN the user types a password under 12 characters, THE SYSTEM
  SHALL block client-side submit and surface a non-blocking inline
  hint, not a server-roundtrip 400.
- WHEN the user types a 12+ character password that the backend
  zxcvbn check still rejects, THE SYSTEM SHALL surface the rejection
  message with at least one concrete improvement suggestion
  ("vermijd je bedrijfsnaam in het wachtwoord").

### B-2 (Likely-blocker) No waitlist → signup bridge

**Current state.** Waitlist submit goes to
`klai-website/src/pages/api/waitlist.ts` → Twenty CRM. Subscriber gets:

- One client-side success state ("Klai isn't live yet. You're first
  on the list. We'll reach out personally when your spot opens up.")
- **Zero email** — no confirmation, no welcome, no double-opt-in.

There is no `signup-invite.py` service in portal-api, no waitlist
sub-state tracking in CRM beyond stage `NEW`. Activating a waitlist
user requires: manual deal review in Twenty → manual email outside
the system → user types from scratch on `my.getklai.com/signup`.

**Why this is "likely-blocker".** Soft-launch works fine at <10 users.
Above that you cannot tell who you have invited, you cannot tell
whether the invite landed, and the silence between waitlist-submit
and "we reach out personally" gets long enough to lose people.

**Acceptance criteria.**
- WHEN a waitlist submit succeeds, THE SYSTEM SHALL send a
  confirmation email to the subscriber within 60 seconds, carrying:
  - acknowledgement copy in their language (NL/EN per `Accept-Language`
    or form field — currently no language detection),
  - an expected-next-step timeline,
  - an unsubscribe link.
- WHEN an admin marks a Twenty CRM `Waitlist –` deal as `INVITED`,
  THE SYSTEM SHALL send a magic-link signup email to the person
  attached to the deal. The link sets a short-lived (24h) signed
  signup-token that pre-fills name + email + company on the signup
  page.
- WHEN the magic-link user completes signup, THE SYSTEM SHALL
  transition the Twenty deal to stage `WON` and emit a
  `waitlist.converted` product event with the source UTM if known.

### B-3 (Blocker) Free-email block fires before personal outreach

**Current state.** `signup.py:163-171` rejects any email whose domain
is in the free-email-provider list (gmail, yahoo, hotmail, …) with
HTTP 400. The error copy is helpful (asks them to use a business
email or request an invitation) but for a softlaunch the "request an
invitation" path does not exist (see B-2).

This means: a warm contact who clicks the magic link in their
personal email cannot complete signup, full stop.

**Acceptance criteria.**
- WHEN a signup uses a free-email domain AND the request carries a
  valid signup-token (B-2), THE SYSTEM SHALL allow the signup.
- WHEN a signup uses a free-email domain AND no token is present,
  THE SYSTEM SHALL keep the current 400 + helpful copy.

### S-1 (Should) No observability for launch-killer paths

**Current state.** Grafana alert files exist for caddy, identity-assert,
infra, ingest, librechat, litellm, login-wall, mailer, orphan-audit,
persistence, portal-api, portal-auth, portal-events (only fires on
`redis_flushall_failed`), portal-mfa, portal-session, privacy,
rag-eval. **Missing:** signup-fail rate, KB-empty queries by new
tenants in their first hour, retrieval fail rate (the silent-degrade
class that bit us 2026-04-28 → 2026-05-05, see `retrieve-caller-
service-header-mismatch` in pitfalls).

If a softlaunch user's first 5 KB queries return empty, we will only
notice when they complain. By then they have already left.

**Acceptance criteria.**
- THE SYSTEM SHALL alert when `event:signup` with
  `properties.success:false` rate > 0 for any 15-minute window.
- THE SYSTEM SHALL alert when a tenant in their first 24 hours
  produces zero `knowledge.queried verified:true` events while
  producing one or more `knowledge.queried` attempts.
- THE SYSTEM SHALL alert when retrieve-call failure rate > 5% over
  any 15-minute window across `litellm`, `partner_chat`, `gap_rescorer`,
  `focus narrow retrieval`.

### S-2 (Should) Trial state UX is invisible

**Current state.** A fresh org row defaults to whatever `PortalOrg.plan`
default is and `billing_status` empty. The portal does not surface
"you are on a trial / N days remain / N% of seats used". After signup
the user lands on a generic dashboard with no progress indicator.

This is fine while users are manually onboarded by Jantine/Steven, but
becomes confusing the moment someone signs up without warm handoff.

**Acceptance criteria.**
- WHEN a new org is created via signup, THE SYSTEM SHALL set
  `billing_status = "trial"` and `trial_ends_at = now + 14 days`
  (or whichever window the launch plan commits to).
- WHEN an authenticated user with `billing_status = "trial"` loads
  the portal, THE SYSTEM SHALL show a non-dismissable banner with
  the remaining trial days and a link to billing setup.
- WHEN `trial_ends_at` passes without a billing mandate, THE SYSTEM
  SHALL freeze write operations and surface a single "convert to
  paid" CTA. (No silent expiry.)

### S-3 (Should) Confirm `settings.mock_billing` cannot be on in prod

**Current state.** `billing.py:62` checks `if settings.mock_billing`
to short-circuit the Moneybird flow and stamp `billing_status="active"`
without any payment. If that flag accidentally ends up `true` in
production environment, every new tenant gets free unlimited access
silently. There is no startup-assertion that rejects this.

**Acceptance criteria.**
- WHEN portal-api starts AND `settings.environment != "test"`,
  THE SYSTEM SHALL refuse to boot if `settings.mock_billing is True`.
- Test coverage: a unit test that boots Settings with
  `MOCK_BILLING=true` and `ENVIRONMENT=production` and asserts
  `ValidationError`.

### N-1 (Nice) GDPR consent + unsubscribe on waitlist

Modal collects name, work email, company, team size with no consent
checkbox and no privacy-link inline. Privacy policy link only appears
on the signup page. For NL/EU launch, add a consent checkbox AND an
unsubscribe path to the confirmation email (which depends on B-2).

### N-2 (Nice) Website "Log in" goes to tenant-specific URL

Nav.astro line 21 points to `https://getklai.getklai.com` — Klai's own
tenant subdomain. It works (Zitadel redirect) but is confusing for
non-Klai-employees. For a softlaunch this is fine; before wider launch
switch to `https://my.getklai.com/`.

### N-3 (Nice) First-run portal walkthrough

No forced onboarding wizard. New user lands on an empty dashboard and
has to guess the next step. Acceptable for hand-held softlaunch (one of
us walks the first 10 through Loom or call). Not acceptable for
self-serve scale.

### N-4 (Nice) Confirmation email branding

Email sent by Zitadel for email-verification still uses Zitadel default
template. Brand it (Klai logo, NL/EN copy, voice).

## Sequencing recommendation

Ship in this order:

1. **B-1** (frontend hint fix) — 1-2h, single file change in
   `signup/index.tsx`. Land before any external user touches signup.
2. **B-3** (free-email bypass for token) — depends on B-2 token logic,
   so land bundled.
3. **B-2** (waitlist → signup bridge) — biggest piece. Includes:
   - mailer template for waitlist-confirmation,
   - mailer template for invite-with-magic-link,
   - Twenty CRM stage transitions API,
   - signup-token signing + verification,
   - `waitlist.converted` product event.
4. **S-3** (mock-billing startup guard) — 15 min, lowest-risk safety.
5. **S-1** (observability alerts) — 1-2h, three new alert YAMLs.
6. **S-2** (trial state) — bigger; touches DB, frontend banner,
   freeze-on-expire flow. Can defer if launch window is short.
7. **N-1..N-4** — post-softlaunch.

## Out of scope (explicitly)

- Launch sequence (who gets invited when, in what order, with which
  messaging) — separate SPEC.
- Pricing-page reorganisation, "Klai isn't live yet" → "Try Klai now"
  copy switch — separate SPEC.
- Onboarding wizard (N-3) — separate SPEC if scope warrants it.
- Billing cycle automation (dunning, payment recovery) — separate SPEC.

## Not blockers (verified working)

- Signup → tenant-provisioning state machine works (SPEC-PROV-001),
  retry endpoint exists, stuck detector runs at startup.
- Social signup (Google, Microsoft) works end-to-end (SPEC-AUTH-001).
- Tenant provisioning rollback chain (compensating transactions) is
  in place.
- Moneybird integration exists with mandate URL, invoice portal,
  cancel flow (`billing.py:51-225`).
- RLS / tenant isolation has been audited multiple times
  (SPEC-TI-001..011, SPEC-SEC-PORTAL-RLS-001).
- VictoriaLogs + Grafana wiring is solid for cross-service tracing.

## Evidence sources

- Code: `klai-website/src/pages/api/waitlist.ts`,
  `klai-website/src/components/ui/WaitlistModal.astro`,
  `klai-website/src/components/sections/Nav.astro`,
  `klai-portal/backend/app/api/signup.py`,
  `klai-portal/backend/app/api/billing.py`,
  `klai-portal/backend/app/services/provisioning/orchestrator.py`,
  `klai-portal/frontend/src/routes/$locale/signup/index.tsx`,
  `deploy/grafana/provisioning/alerting/*.yaml`.
- Live: Playwright walk of `getklai.getklai.com` (redirects to
  `my.getklai.com/login`) and `my.getklai.com/en/signup`
  (2026-05-11).
- Existing pitfalls: `process-rules.md` →
  `retrieve-caller-service-header-mismatch`, `claim-emission-vs-claim-consumption`.
