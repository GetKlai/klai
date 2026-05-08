# DPA addendum — telemetry modes

**Status:** add to the standard Klai DPA from 2026-05-08 onwards
(SPEC-PRIVACY-QUERY-SHADOW-001).

This is the wording the tenant agrees to when they sign the Klai DPA.
It defines the contract for each telemetry mode, the retention
windows, and the tenant's responsibility for `full`-mode debug
sessions.

---

## Article — Telemetry modes for query content (Klai)

The Tenant ("Controller") and Klai BV ("Processor") acknowledge that
Klai's chat platform processes queries submitted by the Tenant's
end-users in order to produce knowledge-base-grounded answers. Klai
records aggregate telemetry about these queries to maintain service
quality and detect retrieval regressions. The level of detail recorded
is configurable per-tenant via the `telemetry_level` setting in the
Tenant's admin console at
`https://<tenant>.getklai.com/admin/settings`.

### Mode definitions

The following three modes are available; the default is `shadow`.

1. **`off`** — Klai records no telemetry derived from the literal
   content of a Tenant query. Aggregate operational logs (latency,
   HTTP status codes, container health) continue to be recorded
   independent of this setting.

2. **`shadow`** (default) — Klai records, per query:
   - a 1024-dimensional dense vector embedding of the query
     (BGE-M3 model);
   - a small set of derived symbolic features: integer token count,
     language tag (`nl` / `en` / `other`), counts of brand-keyword
     mentions, and boolean flags for question-word / URL / email
     pattern occurrence;
   - aggregate retrieval data: confidence band, returned chunk IDs,
     top reranker score.

   The literal query text is **not** stored in any operational
   surface in this mode. Rows are deleted automatically 7 days after
   creation.

3. **`full`** — Klai additionally records the literal text of the
   query in (a) operational logs; (b) the `portal_retrieval_gaps`
   table (when applicable); (c) the in-memory retrieval-log Redis
   blob. All three locations apply a 7-day TTL.

### Retention

Under all three modes, no record derived from the literal content of
a query persists longer than **7 days** in any operational store
managed by Klai.

The 30-day operational-log retention applies only to logs that do not
carry query content (e.g. Caddy access logs, container health, error
traces, metadata-only retrieval events).

The Tenant audit log (`portal_audit_log`) records every
`telemetry_level` change with operator identity and reason; this log
is retained for at least one year as part of the standard audit
trail.

### Default mode

Newly provisioned tenants are configured at `shadow` by default. No
tenant action is required to obtain default privacy posture.

### Switching modes

Either the Tenant (via the admin console) or Klai (via an internal
operator endpoint, with an audit row recording the operator's
identity and a free-text reason) can change the level at any time.
Cache propagation is typically less than one minute.

When Klai initiates a switch to `full` for diagnostic purposes, Klai
will document the reason in the audit row and inform the Tenant
contact within five business days unless the diagnostic relates to
an active security incident, in which case Klai may defer
notification per the standard incident-handling addendum.

### Tenant responsibility under `full`

The Tenant agrees that switching to `full` mode constitutes an
informed decision to retain literal query content (which may include
end-user PII) for 7 days in Klai's operational stores. The Tenant
remains the Controller of this data and is responsible for
documenting the lawful basis under which their end-users' literal
queries are processed by Klai during a `full`-mode session.

Klai will fire an internal alert if a tenant remains in `full` mode
for longer than 14 days; Klai operators will reach out to the Tenant
contact within five business days to confirm whether the debug
session should remain active. The Tenant may close the session at
any time via the admin console.

### Sub-processors and data residency

Klai operates entirely within EU data residency. All shadow-store
records, retrieval-gap rows, retrieval-log blobs, and operational
logs are stored on Klai-managed Postgres / Redis / VictoriaLogs
instances in the EU. No personal data leaves the EU as part of this
processing.

### Right to erasure

The 7-day TTL is the primary privacy fence and applies automatically
to every record under all modes. For `shadow` mode there is no
automated DSAR-purge endpoint because no literal content is stored
to begin with. For `full`-mode records, faster-than-7-day purges
can be requested by contacting Klai's data-protection point of
contact at the address listed in the main DPA.

### Embedding-leakage acknowledgement

The Tenant acknowledges that under `shadow` mode, the stored 1024-
dimensional embedding vector is not perfectly opaque: an attacker
with access to the same model and significant compute could recover
topic-level signal (e.g. "this query was about CRM integrations")
from the vector, but not the literal text. Klai operators have read
access to the embedding column but not to a model-inference pipeline
that reverses embeddings; Klai represents this as "industrial-strength
privacy", not "zero-knowledge". The Tenant agrees that this trade-off
is acceptable for the operational benefit of `shadow`-mode telemetry,
or chooses `off` to eliminate the trade-off entirely.
