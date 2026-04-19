---
id: SPEC-SEC-006
priority: low
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
version: 0.1.0
---

# SPEC-SEC-006: Widget JWT Revocation

## HISTORY

### v0.1.0 (2026-04-19)
- Initial draft based on Phase 3 tenant-isolation audit finding F-008
- Three options evaluated (A short TTL + refresh, B DB cross-check, C JTI blacklist)
- Option B selected as recommended approach (single DB query per auth call, real-time revocation)

---

## Goal

When an admin revokes a widget's access to a knowledge base in the portal, the revocation MUST take effect within one chat request — not after the current JWT expires (up to 1 hour later).

Today the widget session JWT is the source of truth for `kb_ids` for its entire 1-hour TTL. No DB cross-check happens after the JWT is issued. This means any JWT minted before the revocation continues to grant the old, wider scope until natural expiry.

This SPEC closes that gap by re-validating `kb_ids` against the current `widget_kb_access` rows on every authenticated widget request.

## Success Criteria

- Revocation of a widget's KB access takes effect on the very next chat request for that widget session, without waiting for JWT expiry.
- A widget whose entire KB access is revoked mid-session receives HTTP 401 on its next request, with the same opaque error shape already used by `_auth_via_session_token`.
- A widget with partial revocation (some KBs still allowed) keeps working with the narrowed scope.
- No change to JWT generation, TTL, or client-side refresh behaviour — the fix is purely server-side in the auth path.
- Added latency per widget chat request is bounded by a single indexed DB lookup on `widgets` + `widget_kb_access`.

## Environment

- **Service:** `portal-api` (Python 3.12, FastAPI, SQLAlchemy async)
- **Entry point:** `klai-portal/backend/app/api/partner_dependencies.py::_auth_via_session_token`
- **JWT generation:** `klai-portal/backend/app/services/widget_auth.py::generate_session_token` (unchanged by this SPEC)
- **Models:** `app.models.widgets.Widget` (primary key `id` UUID, public id `widget_id` string), `app.models.widgets.WidgetKbAccess` (`widget_id` UUID FK → `Widget.id`, `kb_id` int FK → `portal_knowledge_bases.id`)
- **Database:** PostgreSQL with RLS; `set_tenant(db, org.id)` already called in `_auth_via_session_token`
- **Redis:** Already used for widget rate limiting in the same auth path
- **Caller:** Widget router endpoints in `klai-portal/backend/app/api/partner.py` (the widget_config endpoint generates the JWT)

## Assumptions

- The JWT `wgt_id` claim is the widget's public identifier (the `widget_id` string column on `Widget`, format `wgt_<hex40>`), NOT the internal UUID primary key `Widget.id`.
- `WidgetKbAccess` rows are authoritative for current KB access and are updated synchronously when an admin revokes access via the portal UI.
- The existing `set_tenant(db, org.id)` call in `_auth_via_session_token` applies the RLS context, so the DB cross-check inherits tenant isolation automatically.
- Widget chat requests traverse `portal-api` under the 2-3s user-facing SLA; one additional indexed DB roundtrip (sub-millisecond on local Postgres) is acceptable.
- No widget is expected to have more than a handful of KBs — intersection and comparison are O(small_n).
- `widgets.widget_id` is already indexed (unique index `ix_widgets_widget_id` per the model definition).

## Design Options

Three approaches were considered to close the revocation gap described in F-008.

### Option A — Shorter TTL with refresh endpoint

**Approach:** Reduce JWT TTL from 1 hour to 5-15 minutes and add a refresh endpoint the widget calls to obtain a new JWT before expiry.

**Pros:**
- No per-request DB overhead.
- Works without any change to `_auth_via_session_token`.

**Cons:**
- Still a revocation window (5-15 minutes) — not real-time.
- Requires client-side widget changes to implement refresh logic, and careful handling of network failures during refresh.
- Increases request volume on the widget-config / refresh path.
- Complexity pushed to the embed clients and to every downstream embedder.

### Option B — DB cross-check on every auth call (RECOMMENDED)

**Approach:** In `_auth_via_session_token`, after JWT decode and `set_tenant`, query `widget_kb_access` for the widget identified by the JWT `wgt_id` claim. Intersect the JWT's `kb_ids` with the current DB rows. If the intersection is empty, return 401. Otherwise, populate `kb_access` with only the KBs still present.

**Pros:**
- Real-time revocation — effect is limited to the DB query latency on the next request (typically sub-millisecond).
- Single indexed DB query per widget request; the index on `widgets.widget_id` is already in place.
- No client-side changes; purely server-side fix.
- Simplest implementation — a short block of code in one function.
- No new infrastructure dependency (no Redis blacklist, no refresh endpoint).
- Naturally consistent with how admin operations already work (synchronous DB write).

**Cons:**
- Adds one DB roundtrip per widget chat request. With the widget rate limit at 60 rpm per widget, this is well within the DB's capacity and well within the 2-3s user-facing SLA.

### Option C — JTI blacklist in Redis on revoke

**Approach:** On JWT generation, include a unique `jti` claim and record it (with TTL matching JWT expiry) in Redis. On revoke, write the `jti` to a blacklist set. On each auth call, check Redis for a blacklist hit.

**Pros:**
- Real-time revocation with cheap Redis lookup.
- No DB query on the hot path.

**Cons:**
- Requires a Redis writing path on revoke — but revoke happens via an admin operation that may not already reach into Redis.
- The blacklist must enumerate ALL JWTs for the widget that were minted before revocation — but JWTs are not persisted, so the system does not know which JTIs exist. This reduces Option C to blacklisting the widget itself (by `wgt_id`), which is effectively a single Redis key lookup. Still works, but now we're making Redis the source of truth for revocation state — whereas the DB already is.
- Redis is not a durable source of truth; if Redis loses state (flush, failover), revocations are forgotten.
- More moving parts than Option B for equivalent behaviour.

### Decision

**Option B is recommended.**

Rationale:
- The DB is already the source of truth for `widget_kb_access`. Option B reads directly from source of truth — no synchronisation problem between DB and a cache.
- The cost is a single indexed query per widget request; the widget rate limit (60 rpm) bounds the DB load.
- The change surface is minimal — one function body in one file, plus one integration test.
- Real-time revocation is achieved without any new infrastructure dependency.
- Option A still has a revocation window; Option C introduces a cache-consistency risk and extra infrastructure.

The audit roadmap in `.moai/audit/99-fix-roadmap.md` section SEC-006 reaches the same conclusion.

## Out of Scope

- Changing JWT TTL, algorithm, or claim shape.
- Adding a refresh endpoint or widget-side refresh logic.
- Revoking JWTs for any reason other than KB-access revocation (e.g. widget deletion already works because the `Widget` row no longer resolves — this SPEC does not alter that).
- Caching `widget_kb_access` lookups. Revocation must be real-time; any cache reintroduces the F-008 window. A future perf SPEC may add a short-lived (seconds-scale) cache if DB load ever justifies it.
- Per-request origin re-validation (already handled elsewhere).
- Widget rate limit changes (already in place in the same function).
- Any changes to the JWT generation endpoint (`widget_config` in `partner.py`).

## Security Findings Addressed

- **F-008** (MEDIUM) — Widget JWT has no revocation mechanism. Documented in `.moai/audit/04-tenant-isolation.md` section "F-008 — Widget JWT heeft geen revocation-mechanisme". The finding states that JWT `kb_ids` are not validated against current DB state for the full 1h TTL, so revoked KB access remains usable until natural expiry.
- Roadmap entry: `.moai/audit/99-fix-roadmap.md` section "## SEC-006 — Widget JWT revocation [P3]".

## Requirements

### REQ-1: DB Cross-Check on Widget Auth

The system SHALL re-validate the JWT's `kb_ids` claim against the current `widget_kb_access` rows on every authenticated widget request.

**REQ-1.1:** WHEN `_auth_via_session_token` successfully decodes a widget JWT AND resolves the org, THE service SHALL look up the widget by its public `widget_id` (the JWT `wgt_id` claim) scoped to the resolved org, selecting the internal `Widget.id` (UUID primary key).

**REQ-1.2:** IF the widget cannot be resolved (no row, or row belongs to a different org), THE service SHALL return HTTP 401 with the existing `_AUTH_ERROR` body.

**REQ-1.3:** WHEN the widget is resolved, THE service SHALL query `widget_kb_access` for all rows with `widget_id = Widget.id` and read the current set of `kb_id` values.

**REQ-1.4:** THE service SHALL compute the intersection of the JWT's `kb_ids` claim with the current DB `kb_id` set.

**REQ-1.5:** IF the intersection is empty, THE service SHALL return HTTP 401 with the existing `_AUTH_ERROR` body (opaque "Invalid API key" — do NOT leak whether the cause is revocation vs invalid token).

**REQ-1.6:** IF the intersection is non-empty, THE service SHALL populate `PartnerAuthContext.kb_access` using only the intersection (each entry with `access_level = "read"`, matching current behaviour).

**REQ-1.7:** THE DB cross-check SHALL execute while the RLS tenant context is set (after the existing `set_tenant(db, org.id)` call) so the query is automatically scoped to the resolved org.

### REQ-2: Revoke-Immediate Semantics

The system SHALL ensure revocation takes effect on the next request without requiring JWT expiry.

**REQ-2.1:** WHEN an admin removes a row from `widget_kb_access` for a given widget+kb pair, THE next widget request whose JWT lists only that `kb_id` SHALL be rejected with HTTP 401 (enforced by REQ-1.4 and REQ-1.5).

**REQ-2.2:** WHEN an admin removes a row from `widget_kb_access` for one of several KBs in a JWT, THE next widget request SHALL succeed with `kb_access` narrowed to the remaining intersection (enforced by REQ-1.4 and REQ-1.6).

**REQ-2.3:** THE system SHALL NOT cache `widget_kb_access` lookups in `_auth_via_session_token`. The DB is read per-request.

**REQ-2.4:** THE service SHALL NOT differentiate in log messages or error bodies between "JWT invalid", "widget missing", and "all KBs revoked" — all three produce the same opaque 401. Structured logs MAY bind `wgt_id` and an internal reason code for debugging.

**REQ-2.5:** WHERE downstream widget endpoints consume `PartnerAuthContext.kb_access`, they SHALL reject HTTP 403 if a specific requested `kb_id` is no longer in `kb_access` (this behaviour already exists via REQ-2.5 of SPEC-API-001 and needs no change — the narrowed `kb_access` flows naturally into the existing check).

### REQ-3: Operational and Observability Requirements

**REQ-3.1:** THE added DB query SHALL use the existing `ix_widgets_widget_id` unique index on `widgets.widget_id` for the widget lookup, and the composite primary key of `widget_kb_access` for the access rows.

**REQ-3.2:** THE service SHALL continue to apply rate limiting and `set_tenant` in their current order (rate limit after the KB cross-check is acceptable; the cross-check runs inside the already-tenant-scoped DB session).

**REQ-3.3:** Structured logs SHALL continue to bind `wgt_id` and `org_id` context vars. No new context vars are required.

**REQ-3.4:** SPEC-WIDGET-002 SHALL be cross-referenced in its text to note that `widget_kb_access` revocation is now enforced on the auth path.
