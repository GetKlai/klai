# Research — SPEC-SEC-IMAP-001

## Finding context: #9

From `SECURITY.md` (Cornelis Poppema, 2026-04-22), verified by Claude Opus
on 2026-04-24 against the live code at
`klai-portal/backend/app/services/imap_listener.py:77-107`:

The IMAP listener polls `meet@getklai.com` on `mail.getklai.com:993`,
fetches every `UNSEEN` message, extracts `text/calendar` and `.ics`
attachments, parses them, and feeds the organizer address straight into
the tenant matcher. Zero cryptographic verification of the sender's
authenticity occurs at any point. The RFC-5322 `From` header is consulted
only implicitly (via ICS `ORGANIZER:MAILTO:` extraction inside
`parse_ics`); the header itself is never checked.

An attacker can therefore send any ICS invite with any `ORGANIZER` they
like. If the organizer address matches a real Klai customer, `find_tenant`
at `tenant_matcher.py:32-47` returns a real `(zitadel_user_id, org_id)`
pair, and `schedule_invite` dispatches a Vexa bot into the attacker-
controlled meeting under the victim's identity.

Verdict from the audit response: VERIFIED. Filed as P1 (this SPEC).

## Current flow end-to-end

1. `start_imap_listener` (`imap_listener.py:26`) is an asyncio task spawned
   at portal-api startup. It polls every
   `settings.imap_poll_interval_seconds` (60 s per
   `docker-compose.yml:344`).
2. `_poll_once` (`imap_listener.py:46`) opens IMAP4_SSL to
   `mail.getklai.com:993`, logs in as `meet@getklai.com`, runs `SEARCH
   UNSEEN`, and iterates the result set.
3. For each message ID, `_process_email` (`imap_listener.py:77`) calls
   `imap.fetch(msg_id, "(RFC822)")` — this returns the full raw message
   bytes including ALL headers Cornelis would need to verify.
4. `email.message_from_bytes(raw_bytes)` produces a `Message` object. The
   `DKIM-Signature`, `Authentication-Results`, and `ARC-*` headers are
   all present and readable; they are currently just ignored.
5. `_extract_ics_parts` pulls out any `text/calendar` parts or `.ics`
   attachments.
6. `parse_ics` (in `ical_parser.py`, not modified by this SPEC) produces
   an `Invite` with `organizer_email`.
7. `find_tenant(invite.organizer_email)` — the vulnerability point. The
   address comes from the ICS payload, not from any verified header.
8. On a match, `schedule_invite` dispatches the Vexa bot join.

The fix lands between steps 4 and 5: verify mail-auth on the raw bytes
and the parsed Message object, gate the rest of the function on the
verified identity.

Key observation: the raw RFC-822 bytes ARE available at
`imap_listener.py:85` (`raw_bytes = raw_email[1]`). `authheaders`
verification operates on those bytes directly — no re-serialization
needed. This means we do not lose fidelity due to header folding /
re-parsing round-trips, which is a common pitfall in DKIM verification.

## Candidate library comparison

Four realistic options were considered. Context7 was consulted for
maintenance status of each.

### authheaders (PyPI: `authheaders`)

- **Covers:** DKIM, SPF, DMARC, ARC. Single dependency, single API.
- **Maintenance:** Maintained by `ValiMail` (now `Red Sift`). Last
  release on PyPI 2024+, still receiving updates. License: BSD-3-Clause
  (MIT-compatible).
- **Deps:** Pure Python. Relies on `dnspython` (already transitively
  present in portal-api via `aiosmtplib`) and `py3-dkim` underneath.
- **API fit:** One import, one function per signal. Returns structured
  verdicts that map cleanly onto `MailAuthResult`.
- **Production usage:** Underlies ValiMail's own DMARC products; used by
  several hosted mail reputation services.
- **Risk:** Single-vendor risk is low because it is a thin wrapper over
  IETF-standard primitives; if it is abandoned tomorrow we can swap in
  `dkimpy` directly.

### dkimpy (PyPI: `dkimpy`) + pyspf (PyPI: `pyspf`)

- **Covers:** DKIM-only (`dkimpy`); SPF-only (`pyspf`); no ARC.
- **Maintenance:** `dkimpy` is actively maintained by the OpenDKIM
  project (Scott Kitterman). `pyspf` last meaningful release was 2020,
  widely used but slow-moving.
- **Deps:** `dkimpy` depends on `dnspython`; `pyspf` depends on
  `pydns` (unmaintained). Some distros ship `pyspf` via `PyPI`, others
  as `python3-spf` system package.
- **API fit:** Two imports, two APIs, two result shapes, plus we would
  have to wire up a third lib for ARC (`arcsign` or rolling our own
  from RFC 8617). Triples the integration surface.
- **Risk:** `pyspf`'s dependency on `pydns` is a known pain point —
  builds on Alpine occasionally break.

### dkim-verifier (PyPI: `dkim-verifier`)

- Much less mature. Last commit > 2 years. Not a serious candidate.

### Roll our own

- The DKIM RFC 6376 math is dense (canonicalization, relaxed vs simple,
  tag-list parsing, key discovery via DNS). Writing a correct
  verifier from scratch is a multi-week project with high correctness
  risk. Out of scope for a security-hardening SPEC.

### Decision

Use **`authheaders`**. One dependency, one API, covers all three signals
this SPEC requires (DKIM + SPF + ARC), actively maintained, pure-Python,
permissive license. The single-library choice also matches the
`.claude/rules/klai/lang/python.md` preference for lightweight wrappers
over stitching three packages together with mismatched error models.

Pin: `authheaders>=0.16,<1.0` (lock the 0.x compatibility range; any
major bump gets a fresh SPEC review).

## Upstream mail chain assumptions

REQ-2 leans on `mail.getklai.com` having already validated SPF at
SMTP-accept time and recorded the verdict in the
`Authentication-Results` header. This is a deliberate trust-boundary
choice.

**Why not SPF-check ourselves:** True SPF verification requires the IP
of the SMTP peer that delivered the message. That IP is NOT present in
the message stored on the IMAP server — IMAP delivers the rendered RFC-
822 text, not the SMTP envelope. We could parse the `Received:` chain
and extract the IP, but that chain is attacker-manipulable below the
point where our trusted relay adds its own entry. So the only reliable
source of an SPF verdict is the trusted relay's
`Authentication-Results` header.

**What this requires of `mail.getklai.com`:**
- It SHALL write an `Authentication-Results: mail.getklai.com; ...
  spf=<result> smtp.mailfrom=<domain>` header on every accepted message.
- It SHALL NOT strip or overwrite the sender's `DKIM-Signature` header
  nor the sender's `ARC-*` headers.
- It SHALL be the ONLY entity in the delivery chain that writes an
  `Authentication-Results: mail.getklai.com` header. (Classic ADSP /
  DMARC attack vector: forge an `Authentication-Results` header claiming
  you already passed. `authheaders.authres` filters by the trusted
  authserv-id, so we pass `mail.getklai.com` explicitly.)

**Verification at implementation time:** Pull a recent real message from
the `meet@getklai.com` inbox with `imap.fetch(..., '(RFC822)')`, copy
the full header block into a fixture, and assert the three properties
above. If any fails, we escalate to the infra owner before shipping the
SPEC — this is a prerequisite, not an implementation detail (see
`.claude/rules/klai/pitfalls/process-rules.md` rule `data-before-code`).

If the managed mail host does NOT stamp `Authentication-Results`, REQ-2
collapses to "rely on DKIM+ARC only". That is survivable — DKIM alone
catches the classic spoofing case — but it shrinks defence in depth.

## False-positive risk

The hardest objection to shipping REQ-5 is: "what about legitimate
senders without DKIM?" Analysis:

- **Google Workspace (Gmail, custom domains):** DKIM-signs every
  outbound message by default. Very low FP risk.
- **Microsoft 365:** DKIM-signs by default since 2021. Very low FP risk.
- **iCloud Mail:** DKIM-signs. Low FP risk.
- **Fastmail, Protonmail, Zoho, Hey, etc.:** All DKIM-sign. Low FP risk.
- **Self-hosted Postfix with no DKIM:** Exists in the wild. Effectively
  zero overlap with the Klai customer base (B2B SaaS prospects do not
  run their own mail). Acceptable casualty.
- **Mailing-list forwarders (Google Groups, Microsoft distribution
  lists):** Strip or invalidate original DKIM and re-sign with their own
  domain. REQ-3 (ARC fallback) exists specifically for this case. The
  allowlist covers the common sealers.
- **Forwarded from a personal account** (e.g. a PA forwarding their
  exec's invite from a personal Gmail to the shared inbox): ARC
  covers this if the forwarder is a major provider.

**Mitigation for residual FP risk:** REQ-4.1 emits a queryable
`imap_auth_failed` log with a `reason` code. A spike in a particular
reason (e.g. `arc_untrusted_sealer` from `myprovider.example`) is a
signal to extend the allowlist in REQ-3.4 — config change, no code.

**Monitoring commitment:** Within the first week of production, the
operator SHALL pull the rejection stream via
`service:portal-api AND event:imap_auth_failed` and group by `reason`.
Any reason with > 5% of rejection volume not already covered by a
documented plan SHALL trigger an allowlist review before closing this
SPEC.

## Why reject in the listener, not downstream

A tempting alternative is to pass the mail-auth verdict as a field on
the `Invite` dataclass and reject in `find_tenant`. Rejected: this
keeps `tenant_matcher` a narrow Zitadel-lookup helper and avoids
coupling authentication policy to tenant resolution. The listener is
where untrusted bytes enter the system; it is the right point for the
boundary check. `.claude/rules/klai/pitfalls/process-rules.md`
`minimal-changes` also points this way — the listener's responsibility
is exactly "decide whether to accept this message", and mail-auth is
part of that decision.

## Prior art in the repo

- `klai-portal/backend/app/api/webhooks.py` does constant-time-compare
  webhook secrets (being hardened in SPEC-SEC-WEBHOOK-001). Same shape
  of problem: authenticate the sender before trusting their payload.
  The mail-auth helper here is the email equivalent of that compare.
- `klai-portal/backend/app/services/widget_auth.py` signs JWTs for a
  different trust boundary. Not directly reusable, but demonstrates
  the convention of centralizing auth logic in a single `*_auth.py`
  helper, which we follow with `mail_auth.py`.

## Open questions (tracked, not blocking)

- Should `imap_auth_failed` rejections be rate-limited (emit one log
  per minute per `from_domain`) to avoid log flood during a spoofing
  burst? Deferred — Alloy → VictoriaLogs handles millions of entries/
  day; premature optimisation.
- Should we add a `portal_audit_log` row for every reject, in the
  style of SPEC-SEC-005 AC-1? Deferred — this would require a fake
  `org_id` (no tenant is resolvable for rejected mail) and cross-
  cuts an unrelated table. VictoriaLogs is the forensic surface for
  this SPEC.
- Should ARC-untrusted-sealer be an outright reject, or pass through
  to DKIM/SPF? REQ-3.3 chose pass-through: a sealer we do not yet
  allowlist may still have a directly DKIM-valid inner message, and
  we should not lose that signal.
