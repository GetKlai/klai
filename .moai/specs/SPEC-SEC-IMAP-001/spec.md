---
id: SPEC-SEC-IMAP-001
version: 0.2.0
status: draft
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
priority: high
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-IMAP-001: IMAP Listener DKIM/SPF/ARC Enforcement

## HISTORY

### v0.2.0 (2026-04-24)
- Expanded from stub via `/moai plan SPEC-SEC-IMAP-001`
- Added EARS-format requirements (REQ-1..REQ-5)
- Added research.md (verification library analysis, upstream mail-chain assumptions)
- Added acceptance.md (5 scenarios, each testable with stored fixtures)
- Library decision recorded: `authheaders` (single dependency covering DKIM+SPF+ARC)

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P1
- Expand via `/moai plan SPEC-SEC-IMAP-001`

---

## Findings Addressed

| # | Finding | Severity | Source |
|---|---|---|---|
| 9 | IMAP listener trusts unauthenticated From header; no DKIM/SPF/ARC verification | HIGH | `SECURITY.md` (Cornelis, 2026-04-22), verified by Claude Opus 2026-04-24 |

The current flow at `klai-portal/backend/app/services/imap_listener.py:77-107`
does zero cryptographic verification of the sender. `_process_email` calls
`parse_ics()`, then feeds `invite.organizer_email` straight into
`tenant_matcher.find_tenant()`. The organizer address comes from the ICS
payload (or the RFC-822 `From` header if parsing falls back) — both are
attacker-controlled fields on any delivered message. A crafted invite with
`ORGANIZER:MAILTO:victim@customer.nl` causes the portal to schedule a Vexa
scribe bot under the victim's Zitadel identity and against the victim's
tenant budget, with no upstream auth signal ever consulted.

---

## Goal

Ensure the IMAP listener only processes ICS calendar invites whose sender
identity has been cryptographically verified (DKIM=pass AND SPF alignment,
with ARC accepted as a substitute for legitimately forwarded mail).
Unverified invites SHALL be dropped before any tenant lookup, so attackers
cannot spoof a `From`/`ORGANIZER` address and cause the portal to schedule
a Vexa scribe bot under a victim's identity.

## Success Criteria

- Every inbound email without a DKIM=pass result aligned to the From domain
  is rejected and logged as `imap_auth_failed` with a machine-readable
  `reason` field (see REQ-4).
- SPF alignment with the RFC-5322 `From` domain is enforced on every
  message. Misaligned mail is dropped.
- ARC chain validation is consulted ONLY when DKIM+SPF would otherwise
  fail and the message is a legitimate forward (e.g. mailing-list through
  a customer's distribution group). Required by REQ-3.
- `tenant_matcher.find_tenant` is called only with an organizer email
  whose authenticity is established. Unauthenticated calls are refused
  with a logged warning (REQ-5).
- VictoriaLogs shows a structured `imap_auth_failed` entry for every
  rejection with: `reason` code, `from_header`, `dkim_result`, `spf_result`,
  `arc_result`, `message_id`.
- Regression test fixtures (under
  `klai-portal/backend/tests/services/fixtures/imap/`) cover forged,
  misaligned, valid-DKIM, valid-ARC, and real-world Gmail/Outlook cases.

---

## Environment

- **Service:** `klai-portal/backend` (Python 3.13, FastAPI async app,
  structlog logging)
- **Files in scope:**
  - `klai-portal/backend/app/services/imap_listener.py` — polling loop + `_process_email`
  - `klai-portal/backend/app/services/ical_parser.py` — ICS extraction (organizer)
  - `klai-portal/backend/app/services/tenant_matcher.py` — consumer of the organizer address (`find_tenant`)
  - `klai-portal/backend/pyproject.toml` — new dependency `authheaders`
  - `klai-portal/backend/app/core/config.py` — IMAP verification flag(s)
  - New helper: `klai-portal/backend/app/services/mail_auth.py`
- **IMAP inbox:** `meet@getklai.com` on `mail.getklai.com:993`
  (IMAP4_SSL) — see `deploy/docker-compose.yml:341-345`
- **Upstream mail chain:** External senders → external MX →
  `mail.getklai.com` mailbox (managed mail host, operator-controlled).
  Verification of `Authentication-Results` trust boundary is tracked in
  research.md under "Upstream mail chain assumptions".
- **Observability:** structlog JSON via Alloy → VictoriaLogs (30d
  retention). `service:portal-api` stream. See
  `.claude/rules/klai/infra/observability.md`.

## Assumptions

- Klai's IMAP inbox receives mail through a relay that preserves the
  original RFC-822 headers (`DKIM-Signature`, `Received-SPF`,
  `Authentication-Results`, `ARC-*`) unmodified and in their original
  order. To be verified at implementation time by pulling a real message
  from `meet@getklai.com` and inspecting the header block.
- `authheaders` (pure-Python, MIT license, covers DKIM+SPF+ARC) is
  available on PyPI and deployable in the portal-api Docker image without
  C toolchain changes. Confirmed in research.md.
- Rejecting unauthenticated invites does not break any legitimate use
  case: every Klai customer emails invites from a DKIM-signing provider
  (Google Workspace, Microsoft 365, iCloud, Fastmail). A domain that does
  NOT sign outbound mail is an anti-signal and is out of scope for auto-
  scheduling.
- The IMAP listener is the only ingress that consumes untrusted mail into
  the scribe-scheduling path. Webhook invites (future SPEC) are out of
  scope here.

## Out of Scope

- DMARC aggregate/forensic reporting and inbox quarantine tooling
  (future SPEC).
- Replacing IMAP polling with a webhook-based invite ingestion. A separate
  strategic discussion (tracked, not blocking) — see
  `.claude/rules/klai/pitfalls/process-rules.md` for the adapter-framework-
  bleed rationale.
- Anti-spam, anti-phishing, or body-content analysis. This SPEC is
  narrowly about sender authenticity.
- Outbound mail signing. Portal does not send meeting invites outbound
  from the listener.
- Migrating away from the `mail.getklai.com` provider. Any verification
  headers it rewrites must be documented, not fought.

---

## Threat Model

The adversary can send email with any `From:` / `ORGANIZER:` address they
choose. The portal currently trusts both. The realistic attack chain:

1. Attacker sends a crafted ICS invite to `meet@getklai.com` with
   `ORGANIZER:MAILTO:ceo@customer.nl` and any meeting URL of their
   choosing.
2. `_process_email` parses the ICS, obtains `ceo@customer.nl`,
   passes it to `find_tenant`.
3. `find_tenant` hits Zitadel, matches a real user, returns
   `(zitadel_user_id, org_id)`.
4. `schedule_invite` dispatches a Vexa bot into the attacker's chosen
   meeting URL under the victim's identity and spend.

Consequences: unauthorised bot joins an attacker-controlled meeting,
exfiltrates any audio/transcript it records, consumes victim's Vexa
budget, pollutes victim's `product_events` stream with impersonated
`meeting.*` events.

After this SPEC:
- DKIM=pass aligned to the `From` domain is required, OR
- The RFC-5322.From domain is covered by a valid ARC chain from a trusted
  sealer (forwarded mail case).
- Mail without both signals drops before `parse_ics` extracts anything
  usable.
- Forged-From messages emit `imap_auth_failed` with a `reason` code and
  are visible in VictoriaLogs within seconds.

Explicit non-goals:

- Defeating an adversary who compromises a DKIM-signing key at a Klai
  customer (e.g. Google Workspace tenant takeover). Out of scope — this
  is an identity-provider compromise, not a mail-auth gap.
- Preventing legitimate misconfiguration (customer disables their own
  DKIM). REQ-5 drops their mail; the operator fixes the customer's DNS.

---

## Requirements

### REQ-1: DKIM=pass Enforcement Aligned to From Domain

The system SHALL require a valid DKIM signature that aligns with the
RFC-5322 `From` header domain before any downstream processing.

- **REQ-1.1:** WHEN `_process_email` receives a message, THE service
  SHALL extract the RFC-5322 `From` header and normalize the domain to
  lowercase before any ICS parsing.
- **REQ-1.2:** WHEN the message contains one or more `DKIM-Signature`
  headers, THE service SHALL invoke DKIM verification via the
  `authheaders` library AND SHALL accept the message only IF at least one
  signature verifies AND the `d=` parameter of a verifying signature
  aligns with the From domain (exact match OR organizational-domain
  match per RFC 7489 §3.1.1).
- **REQ-1.3:** WHEN the message contains zero `DKIM-Signature` headers,
  THE service SHALL reject the message via REQ-5 UNLESS REQ-3 (ARC
  fallback) applies.
- **REQ-1.4:** The DKIM verification call SHALL be run with a per-message
  wall-clock timeout of 5 seconds via `asyncio.wait_for`. IF the timeout
  is hit, THE service SHALL treat the result as `dkim=timeout` and reject
  via REQ-5.
- **REQ-1.5:** The helper `verify_mail_auth(raw_message: bytes) ->
  MailAuthResult` SHALL return a structured result dataclass containing
  `dkim_result`, `spf_result`, `arc_result`, `from_domain`,
  `verified_from: str | None`, `reason: str`. Downstream code gates on
  `verified_from is not None`.

### REQ-2: SPF Alignment with From Domain

The system SHALL verify SPF against the connecting IP recorded in the
trusted `Received-SPF` / `Authentication-Results` header from the
upstream relay, aligned to the RFC-5322 `From` domain.

- **REQ-2.1:** WHEN the upstream `Authentication-Results` header from
  `mail.getklai.com` contains `spf=pass` with an `smtp.mailfrom` domain
  that aligns (exact OR organizational-domain match) with the RFC-5322
  `From` domain, THE service SHALL accept the SPF signal as a positive
  alignment.
- **REQ-2.2:** WHEN SPF is absent OR `spf=fail` OR `spf=softfail` OR
  the `smtp.mailfrom` domain does not align to the From domain, THE
  service SHALL treat SPF as NOT aligned. SPF is a soft signal in this
  SPEC: DKIM=pass alone is a sufficient positive verdict per REQ-1.2;
  SPF misalignment alone is not a reject reason.
- **REQ-2.3:** WHEN both DKIM and ARC fail to produce a positive verdict
  (REQ-1, REQ-3) AND SPF is not aligned (REQ-2.1), THE service SHALL
  reject the message via REQ-5 with `reason="no_auth_signal"`.
- **REQ-2.4:** The helper SHALL NOT perform SPF DNS lookups itself on
  raw MAIL-FROM. Enforcement relies on `mail.getklai.com` having already
  validated SPF at SMTP-accept time and recorded it in the
  `Authentication-Results` header. This is an explicit trust-boundary
  choice (see research.md).

### REQ-3: ARC Validation for Legitimately Forwarded Mail

The system SHALL validate ARC chains when present and accept them as a
substitute for direct DKIM alignment on forwarded mail.

- **REQ-3.1:** WHEN the message contains `ARC-Seal`, `ARC-Message-
  Signature`, and `ARC-Authentication-Results` headers, THE service SHALL
  validate the ARC chain via `authheaders.arc_verify`.
- **REQ-3.2:** WHEN the ARC chain is valid AND the sealing domain (`d=`
  of the outermost valid `ARC-Seal`) is a Klai-maintained allowlist of
  trusted ARC sealers (initial list: `google.com`, `outlook.com`,
  `icloud.com`, `fastmail.com`, `protonmail.ch`), THE service SHALL treat
  the `ARC-Authentication-Results` record from the innermost hop as
  authoritative for DKIM/SPF alignment.
- **REQ-3.3:** WHEN the ARC chain is invalid OR the sealing domain is
  not in the allowlist, THE service SHALL NOT accept the ARC signal AND
  SHALL fall through to the REQ-1 / REQ-2 verdict.
- **REQ-3.4:** The allowlist of trusted ARC sealers SHALL be configurable
  via `settings.imap_trusted_arc_sealers: list[str]` (pydantic-settings),
  default to the initial list in REQ-3.2.

### REQ-4: Structured Logging for Every Rejection

The system SHALL emit a structlog entry at `warning` level for every
rejected message with stable, queryable fields.

- **REQ-4.1:** WHEN a message is rejected by REQ-1/REQ-2/REQ-3, THE
  service SHALL emit `logger.warning("imap_auth_failed", ...)` with the
  following fields: `reason` (enum: `no_dkim_signature`,
  `dkim_invalid`, `dkim_misaligned`, `spf_misaligned`, `arc_invalid`,
  `arc_untrusted_sealer`, `no_auth_signal`, `dkim_timeout`,
  `malformed_headers`), `from_header` (raw RFC-5322 From value),
  `from_domain`, `dkim_result`, `spf_result`, `arc_result`,
  `message_id` (RFC-5322 Message-ID or `"<unknown>"` if absent).
- **REQ-4.2:** The log entry SHALL NOT include the email body, ICS
  payload, or any attachment content. Reject decisions MUST be
  debuggable from headers alone.
- **REQ-4.3:** WHEN a message passes verification, THE service SHALL
  emit a structlog `info` entry `imap_auth_passed` with
  `verified_from`, `from_domain`, and the same result fields. This
  positive trail is required for post-incident forensics.

### REQ-5: No Tenant Lookup on Unauthenticated Mail

The system SHALL NOT call `tenant_matcher.find_tenant` for any message
that has not passed mail-auth verification.

- **REQ-5.1:** WHEN `verify_mail_auth` returns `verified_from is None`,
  THE `_process_email` function SHALL `return` before `parse_ics` is
  called AND SHALL NOT invoke `find_tenant` NOR `schedule_invite`.
- **REQ-5.2:** WHEN `verify_mail_auth` returns a non-None
  `verified_from`, THE `_process_email` function SHALL use
  `verified_from` (NOT the ICS `ORGANIZER` field) as the argument to
  `find_tenant`. The RFC-5322 `From` domain is the authoritative sender
  identity; `ORGANIZER` is an attacker-controlled field inside the
  attachment.
- **REQ-5.3:** WHEN `parse_ics` produces an `invite.organizer_email`
  whose domain does NOT match `verified_from`'s domain, THE service
  SHALL log a `warning` with `event="imap_organizer_mismatch"` (fields:
  `verified_from`, `ics_organizer`) AND SHALL proceed using
  `verified_from`. The invite is not rejected, but the mismatch is
  audited. Rationale: a minority of legitimate calendar clients
  (notably some ActiveSync variants) put a delegated organizer in the
  ICS while the email is sent from the delegate's mailbox; we must not
  break these flows, but we must see them.
- **REQ-5.4:** The message-seen flag (`\\Seen` at
  `imap_listener.py:69`) SHALL still be set for rejected messages so the
  listener does not reprocess them on the next poll.

---

## Non-Functional Requirements

- **Performance:** Verification adds no more than 50 ms p95 per message
  on the happy path. The 5 s timeout in REQ-1.4 is a hard ceiling for
  the slow path; the mean DKIM verify with a cached DNS resolver is
  sub-10 ms.
- **Availability:** If `authheaders` raises an unexpected exception,
  the caller SHALL treat the result as `reason="malformed_headers"` and
  reject the message (fail-closed). Unlike rate limiting, fail-open is
  wrong here: the whole point is to refuse unverifiable mail.
- **Observability:** Stable log keys to alert on:
  `imap_auth_failed` (grouped by `reason`), `imap_organizer_mismatch`.
  A spike in `reason=dkim_timeout` suggests DNS issues on the portal-api
  container; a spike in `reason=arc_untrusted_sealer` suggests a new
  legitimate sealer that should be added to the allowlist.
- **Privacy:** Verification consumes RFC-822 headers only. Body content
  is never read by the mail-auth helper. The helper's inputs SHALL NOT
  be logged.
- **Backward compatibility:** Legitimate senders on Google Workspace,
  Microsoft 365, iCloud, and Fastmail — which cover the entire paying
  Klai customer base as of 2026-04-24 — continue to be processed. This
  is a hard regression: if a customer's invite stops scheduling a Vexa
  bot after this SPEC, that is a defect.

---

## Proposed Approach (high-level — detailed in plan.md)

1. Add `authheaders` to `klai-portal/backend/pyproject.toml` under the
   main dependency group; pin to a known-good minor version.
2. Create `klai-portal/backend/app/services/mail_auth.py` exposing
   `verify_mail_auth(raw_message: bytes) -> MailAuthResult`. Keep DKIM
   / SPF-header-parsing / ARC logic behind a single function so the
   call site stays a one-liner.
3. Gate `_process_email` on the helper's `verified_from`. Use
   `verified_from` as the input to `find_tenant` (REQ-5.2). Keep the
   ICS organizer mismatch warning non-fatal (REQ-5.3).
4. Emit the two stable structlog keys `imap_auth_failed` and
   `imap_auth_passed` (REQ-4).
5. Add regression test fixtures under
   `klai-portal/backend/tests/services/fixtures/imap/` with the five
   scenarios in acceptance.md.

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Audit source: `SECURITY.md` (Cornelis Poppema, 2026-04-22), finding #9
- Related pitfall: `data-before-code` in
  `.claude/rules/klai/pitfalls/process-rules.md` — pull a real sample
  message before choosing library defaults
- Related rule: `.claude/rules/klai/projects/portal-logging-py.md` —
  structlog conventions for `imap_auth_*` keys
- Observability chain: `.claude/rules/klai/infra/observability.md` —
  LogsQL query patterns for `service:portal-api AND event:imap_auth_failed`
