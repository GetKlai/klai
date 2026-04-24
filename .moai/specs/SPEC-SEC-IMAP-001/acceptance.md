# Acceptance Criteria — SPEC-SEC-IMAP-001

EARS-format acceptance tests that MUST pass before SPEC-SEC-IMAP-001 is
considered complete. Each item is verifiable against
`klai-portal/backend/app/services/imap_listener.py`, the new
`klai-portal/backend/app/services/mail_auth.py` helper, stored email
fixtures under `klai-portal/backend/tests/services/fixtures/imap/`, and
the structlog output captured via `caplog`.

Fixtures are raw RFC-822 bytes captured from real messages (or, where a
real capture is unsafe, generated via `dkimpy`'s signing utilities
against a throwaway key and then forged where the scenario demands).

## AC-1: Forged From header, no DKIM signature

Scenario: an attacker sends an ICS invite with `From:
ceo@customer.nl` and no DKIM signature at all.

- **WHEN** `_process_email` receives a message with RFC-5322 `From`
  header set to `ceo@customer.nl` AND no `DKIM-Signature` header AND
  no valid ARC chain **THE** listener **SHALL** reject the message
  before `parse_ics` is invoked.
- **THE** listener **SHALL NOT** call `tenant_matcher.find_tenant`
  **AND SHALL NOT** call `invite_scheduler.schedule_invite`.
- **THE** service **SHALL** emit exactly one structlog entry at level
  `warning` with `event="imap_auth_failed"` AND
  `reason="no_dkim_signature"` AND `from_header="ceo@customer.nl"`
  AND `from_domain="customer.nl"`.
- **THE** listener **SHALL** still mark the message as `\Seen` in
  IMAP so it is not reprocessed on the next poll (REQ-5.4).

Fixture: `fixtures/imap/forged_no_dkim.eml`. Assertion: patch
`find_tenant` with `MagicMock`, assert `mock.called is False`.

## AC-2: Valid DKIM signature but misaligned SPF

Scenario: a message with a valid DKIM signature for `d=spammer.net`
but `From: ceo@customer.nl`. DKIM passes cryptographically, but the
signing domain does not align to the From domain.

- **WHEN** `_process_email` receives a message with a `DKIM-Signature`
  that cryptographically verifies with `d=spammer.net` AND an RFC-5322
  `From` domain of `customer.nl` AND no other positive signal **THE**
  listener **SHALL** reject the message.
- **THE** service **SHALL** emit `event="imap_auth_failed"` with
  `reason="dkim_misaligned"` AND
  `dkim_result={"valid": true, "d": "spammer.net", "aligned": false}`.
- **THE** listener **SHALL NOT** call `find_tenant` NOR
  `schedule_invite`.

Fixture: `fixtures/imap/dkim_valid_misaligned.eml`. Signed against a
test key owned by the test suite; DNS lookup is mocked to return the
matching public key for `d=spammer.net`.

Note: SPF alignment per REQ-2.2 is a soft signal, so the reject
reason here is DKIM misalignment, not SPF. The spec requires DKIM=pass
ALIGNED to the From domain; a DKIM=pass misaligned to From fails REQ-
1.2's alignment check even though the signature itself is valid.

## AC-3: Valid DKIM + SPF + ARC — processed normally

Scenario: a well-formed invite from a Klai customer sent via Google
Workspace with full DKIM signature aligned to the From domain, SPF
pass recorded by `mail.getklai.com`, and an ARC seal from Google.

- **WHEN** `_process_email` receives a message with
  `From: boss@customer.nl` AND a DKIM signature with `d=customer.nl`
  that verifies AND an `Authentication-Results: mail.getklai.com; ...
  spf=pass smtp.mailfrom=customer.nl` header AND a valid ARC chain
  from `d=google.com` **THE** listener **SHALL** accept the message.
- **THE** service **SHALL** emit exactly one structlog entry at level
  `info` with `event="imap_auth_passed"` AND
  `verified_from="boss@customer.nl"` AND
  `from_domain="customer.nl"`.
- **THE** listener **SHALL** call
  `find_tenant("boss@customer.nl")` exactly once (REQ-5.2 — input is
  `verified_from`, not the ICS `ORGANIZER` field).
- **WHEN** `find_tenant` returns a match **THE** listener **SHALL**
  call `schedule_invite` exactly once with that tenant.

Fixture: `fixtures/imap/google_valid.eml`. Captured from a real
Google-Workspace-sent invite (sanitised for test commit).

## AC-4: Gmail-sent invite — typical real-world case

Scenario: the most common legitimate path. A customer sends an invite
from a personal Gmail address (`someone@gmail.com`) to
`meet@getklai.com`. DKIM signature is `d=gmail.com`, aligned.
`Authentication-Results` records `spf=pass smtp.mailfrom=gmail.com`.

- **WHEN** `_process_email` receives a Gmail-signed invite with all
  three signals positive **THE** listener **SHALL** accept the
  message AND **SHALL** emit `imap_auth_passed` AND **SHALL** proceed
  to `find_tenant`.
- **WHEN** `find_tenant("someone@gmail.com")` returns `None` (the
  sender is not a registered Klai user) **THE** listener **SHALL**
  silently skip the message (existing behaviour at
  `imap_listener.py:103-104`). No `imap_auth_failed` is emitted,
  because the auth check passed — the tenant just isn't ours.
- **WHEN** `find_tenant("someone@gmail.com")` returns a valid tenant
  tuple **THE** listener **SHALL** proceed to `schedule_invite`.

Fixture: `fixtures/imap/gmail_valid.eml`. Verifies that the helper
does not over-fit to the corporate-domain case; consumer Gmail must
work identically.

## AC-5: Forged From, valid ARC from trusted sealer — processed as forwarded mail

Scenario: a message where the original DKIM is broken (common on
mailing-list forwards), but a valid ARC chain from `d=google.com`
vouches for the original sender.

- **WHEN** `_process_email` receives a message with `From:
  boss@customer.nl` AND a broken (re-written) DKIM signature AND a
  valid ARC chain from `d=google.com` where the innermost
  `ARC-Authentication-Results` records
  `dkim=pass header.d=customer.nl` AND `d=google.com` is in
  `settings.imap_trusted_arc_sealers` **THE** listener **SHALL**
  accept the message.
- **THE** service **SHALL** emit `event="imap_auth_passed"` AND
  **SHALL** include `arc_result={"valid": true, "sealer":
  "google.com", "trusted": true}`.
- **THE** listener **SHALL** call
  `find_tenant("boss@customer.nl")`.

Fixture: `fixtures/imap/arc_forwarded_google.eml`.

## AC-6: ARC from untrusted sealer — falls through to DKIM/SPF verdict

Scenario: a message with a valid ARC chain but from a sealer NOT in
the allowlist (e.g. `d=weird-provider.example`). Direct DKIM is
broken and SPF is misaligned.

- **WHEN** `_process_email` receives a message with a valid ARC chain
  from `d=weird-provider.example` (not in the allowlist) AND no other
  positive signal **THE** listener **SHALL** reject the message.
- **THE** service **SHALL** emit `event="imap_auth_failed"` with
  `reason="arc_untrusted_sealer"` AND `arc_result={"valid": true,
  "sealer": "weird-provider.example", "trusted": false}`.

Fixture: `fixtures/imap/arc_untrusted.eml`.

## AC-7: ICS organizer mismatch is warned but not fatal

Scenario: a delegated-calendar setup where the email is sent from
`pa@customer.nl` (DKIM-valid, aligned), but the ICS `ORGANIZER:` is
`boss@customer.nl`.

- **WHEN** the DKIM-verified `from_domain` is `customer.nl` AND the
  ICS `organizer_email` parsed by `parse_ics` is `boss@customer.nl`
  (same domain, different local part) **THE** listener **SHALL** emit
  exactly one structlog `warning` with
  `event="imap_organizer_mismatch"` AND fields
  `verified_from="pa@customer.nl"` AND
  `ics_organizer="boss@customer.nl"`.
- **THE** listener **SHALL** proceed using `verified_from`
  (`pa@customer.nl`) as the argument to `find_tenant` (REQ-5.3).
- **WHEN** the ICS `organizer_email` domain does NOT match
  `verified_from`'s domain (e.g. `attacker@evil.com`) **THE**
  listener **SHALL** still emit the mismatch warning AND **SHALL**
  still proceed using `verified_from`. The ICS field is informational;
  mail-auth is authoritative.

Fixture: `fixtures/imap/pa_delegated.eml`.

## AC-8: Verification timeout

Scenario: `authheaders.dkim_verify` hangs (e.g. DNS resolver wedged).
The 5-second timeout should kick in and treat the message as
unverifiable.

- **WHEN** the DKIM verification call does not return within 5 seconds
  **THE** listener **SHALL** emit `event="imap_auth_failed"` with
  `reason="dkim_timeout"` AND **SHALL** reject the message.

Test method: monkey-patch the verify helper with
`asyncio.sleep(10)`; wrap the call in `asyncio.wait_for(..., 5)`.

## AC-9: Malformed headers fail closed

Scenario: a message with a syntactically broken `DKIM-Signature`
header that causes `authheaders` to raise an unexpected exception.

- **WHEN** the mail-auth helper raises any unhandled exception during
  verification **THE** listener **SHALL** catch it AND **SHALL** emit
  `event="imap_auth_failed"` with `reason="malformed_headers"` AND
  **SHALL** reject the message (fail-closed).
- **THE** listener **SHALL NOT** crash the polling loop. The next
  iteration of `_poll_once` **SHALL** proceed normally.

Fixture: `fixtures/imap/malformed_dkim.eml`.

## AC-10: tenant_matcher.find_tenant is never called on unauthenticated mail

Cross-cutting contract spanning AC-1, AC-2, AC-6, AC-8, AC-9.

- **FOR** every rejection scenario in this document **THE** test
  **SHALL** patch `tenant_matcher.find_tenant` with a `MagicMock` AND
  **SHALL** assert `find_tenant.called is False`.
- **FOR** every accept scenario in this document **THE** test
  **SHALL** assert `find_tenant.called is True` with the positional
  argument equal to the verified From address (not the ICS
  organizer).

## AC-11: Structured log schema stability

- **WHEN** any `imap_auth_failed` entry is emitted **THE** entry
  **SHALL** contain exactly these top-level keys: `event`, `reason`,
  `from_header`, `from_domain`, `dkim_result`, `spf_result`,
  `arc_result`, `message_id`. No request body, no ICS payload, no
  attachment content (REQ-4.2).
- **WHEN** any `imap_auth_passed` entry is emitted **THE** entry
  **SHALL** contain exactly these top-level keys: `event`,
  `verified_from`, `from_domain`, `dkim_result`, `spf_result`,
  `arc_result`, `message_id`.
- **WHEN** either entry is queried in VictoriaLogs via
  `service:portal-api AND event:"imap_auth_failed"` **THE** stream
  **SHALL** return matches for every rejection produced in the test
  run. Integration is smoke-tested via `caplog` in unit tests;
  production verification is post-deploy.
