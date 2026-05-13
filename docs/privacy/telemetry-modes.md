# Privacy modes — what Klai records about your users' queries

**Status:** live since 2026-05-08 (SPEC-PRIVACY-QUERY-SHADOW-001).
This page is normative — the wording here defines the contract between
Klai and our tenants about each telemetry mode.

## Why three modes

A privacy-friendly EU-only AI platform has to balance two real
requirements:

- **Data minimization** (GDPR Article 5(1)(c)) — operators should not
  hold customer-facing query content longer than necessary.
- **Operational visibility** — we need *some* signal about how
  retrieval is performing, otherwise a regression at one tenant can go
  undetected for weeks (we lived this in April 2026, see PR #517).

The compromise: per-tenant choice between three modes, with a
privacy-friendly default. **The literal text of your users' queries is
never persisted by Klai for longer than 7 days under any mode**, and in
the default mode it is never persisted at all.

## The modes

### `off` — minimum surface

Klai stores **nothing** about the queries your users submit. No vector
fingerprint, no length statistics, no gap-event row. We cannot do
quality monitoring, support-team incident triage, or any kind of
aggregate analysis for your tenant.

The Klai chat product still works fully (retrieval, answer synthesis,
template injection); only the operator-side observability is removed.
Thumbs-up / thumbs-down feedback is unaffected — that lives on
different data and is keyed to the response, not the query.

**Use when:** your end-users' queries contain content you cannot
share with any third-party operator, even in anonymised form.

### `shadow` (default) — anonymous fingerprint, 7d

Klai stores, for each `/retrieve` call:

- **Embedding vector** (1024 floats from BGE-M3 — a multilingual
  multimodal text embedder). The vector represents the query in a
  high-dimensional semantic space; with access to the same model and
  significant compute, an attacker can recover topic-level signal
  ("this query was about CRM integrations") but not literal PII.
- **Symbolic features**: integer token count, language-detection tag
  (`nl` / `en` / `other`), boolean flags for whether a brand keyword
  appeared (and how many), whether the query started with a question
  word, whether a URL was mentioned, whether an email-shaped substring
  was mentioned.
- **Aggregate retrieval data**: confidence band, retrieved chunk IDs,
  top reranker score.

**The literal query text is never persisted** in this mode. The
shadow row is keyed by request-ID and tagged with the tenant's org-ID;
all rows are deleted automatically after **7 days**. The deletion is
mechanical (a daily cron) — no operator action is required to trigger
it.

What we can do with this data:
- Detect retrieval-quality regressions (band distribution shifts)
- Cluster failure cases by topic (find systematic gaps without seeing
  what any single user asked)
- Match a new query to known-failing-cluster nearest neighbours

What we cannot do:
- Reconstruct the literal text of a specific query
- Answer "what did user X ask on date Y" — by design

**Use when:** you want default-good observability without
literal-text retention. Recommended for every tenant unless you have
a specific reason to pick a different mode.

### `full` — opt-in debug mode, 7d, audit-trailed

Klai stores everything `shadow` records, **plus** the literal query
text in:

- The `decision_record` event in our log pipeline (VictoriaLogs)
- The `query_text` column of `portal_retrieval_gaps` (when retrieval
  produces a low-confidence result)
- The `query_resolved` field of the in-Redis retrieval-log JSON blob

All three with **7-day TTL**. Nothing under any mode survives 7 days.

`full` mode is opt-in:

- Either the tenant flips it themselves via
  `https://<your-tenant>.getklai.com/admin/settings`, or
- A Klai operator flips it explicitly in response to a debug request,
  and the audit-log records the operator's identity + reason.

Both paths produce a row in `portal_audit_log`. After 14 days, our
operations team gets a `privacy_tenant_stuck_in_full` alert if a
tenant is still in `full` mode — operators are expected to either
confirm the debug session is still active or flip the tenant back.

**Use when:** you or Klai are actively investigating a specific issue
and need to see the literal text of failing queries. Don't forget to
flip back when done.

## What is *never* recorded — under any mode

- Authentication credentials, tokens, OAuth refresh tokens
- LibreChat conversation history (the chat UI's own data lives in your
  tenant's MongoDB; Klai operators do not access it)
- Anything that would normally be classed as PII outside the query
  itself (we don't enrich with IP geolocation, user-agent strings, etc.
  on the privacy-sensitive paths)

## Where this is enforced

- Application code: every retrieval-pipeline log site that touches
  query content is gated on `telemetry_level`. A defense-in-depth
  structlog processor strips raw-query-shaped fields from any future
  log line that was not explicitly gated.
- Database: a daily cron job deletes rows older than 7 days from the
  shadow store and from `portal_retrieval_gaps`.
- Audit: every level change is recorded in `portal_audit_log` with
  the operator (or tenant-admin) identity and the reason.
- Observability: a per-tenant privacy dashboard lets us spot tenants
  whose retention diverges from policy.

## Questions or audit requests

For SARs and similar GDPR-driven requests, see the standard SAR
endpoint exposed under `/api/me/sar-export`. The 7-day TTL is the
primary privacy fence; faster purges for `full`-mode tenants can be
arranged out-of-band (no automated DSAR-purge endpoint in v1).
