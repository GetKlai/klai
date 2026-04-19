# SPEC-SEC-006 — Acceptance Criteria

EARS-format acceptance criteria for widget JWT revocation via DB cross-check (Option B).

## A1 — DB cross-check happens on every widget auth

- **WHEN** a widget JWT is validated in `_auth_via_session_token` **THE** service **SHALL** fetch current `WidgetKbAccess` rows from the DB matching the widget identified by the JWT `wgt_id` claim.
- **WHEN** the widget lookup runs **THE** service **SHALL** execute the query under the RLS tenant context set by the preceding `set_tenant(db, org.id)` call.

## A2 — Widget resolution failure → 401

- **IF** no `Widget` row matches the JWT `wgt_id` within the resolved org **THE** service **SHALL** return HTTP 401 with the existing opaque `_AUTH_ERROR` body.
- **IF** the resolved `Widget` row belongs to a different org than the JWT `org_id` claim **THE** service **SHALL** return HTTP 401 (RLS already enforces this, and the explicit org_id filter belt-and-braces it).

## A3 — Intersection logic

- **WHEN** the widget resolves and its `WidgetKbAccess` rows are read **THE** service **SHALL** compute the intersection of the JWT `kb_ids` claim with the current DB `kb_id` set.
- **IF** a `kb_id` in the JWT is no longer in the DB's current access list **THE** service **SHALL** filter it out of `PartnerAuthContext.kb_access`.
- **IF** the intersection is non-empty **THE** service **SHALL** populate `kb_access` with `{kb_id: "read"}` entries for only the intersection members.

## A4 — Full revocation → 401

- **IF** all JWT `kb_ids` are revoked (intersection is empty) **THE** service **SHALL** return HTTP 401 with the opaque `_AUTH_ERROR` body (`"Invalid API key"`).
- **WHILE** returning 401 **THE** service **SHALL NOT** distinguish in the error body or HTTP status between "JWT invalid", "widget not found", and "all KBs revoked".

## A5 — Partial revocation → narrowed scope

- **WHEN** some but not all JWT `kb_ids` have been revoked **THE** service **SHALL** return a valid `PartnerAuthContext` whose `kb_access` contains only the still-permitted `kb_id` values.
- **WHEN** a downstream widget endpoint requests a `kb_id` that is NOT in the narrowed `kb_access` **THE** existing permission check in SPEC-API-001 REQ-2.5 **SHALL** return HTTP 403 (no new logic needed; narrowed `kb_access` flows in naturally).

## A6 — Revoke-immediate integration test

- **WHEN** an integration test revokes a widget's sole KB access by deleting the `widget_kb_access` row mid-session **AND** the widget's next chat request arrives with the previously-valid JWT **THE** service **SHALL** return HTTP 401 — without waiting for JWT expiry.
- **WHEN** an integration test revokes one of two KBs in a widget's JWT mid-session **AND** the widget's next chat request references the still-permitted KB **THE** service **SHALL** succeed with narrowed `kb_access`.
- **WHEN** the same integration test then requests the revoked KB **THE** service **SHALL** return HTTP 403.

## A7 — Latency budget

- **WHILE** the cross-check is active **THE** added auth-path latency **SHALL** be bounded by a single indexed `SELECT` on `widgets` plus a single `SELECT` on `widget_kb_access` (both using existing indexes).
- **WHILE** no cache is introduced **THE** revocation window **SHALL** be zero beyond DB query latency.

## A8 — No new failure modes

- **WHEN** the DB is reachable **THE** cross-check **SHALL NOT** introduce new 5xx failure paths. DB errors SHALL propagate through existing SQLAlchemy error handling (same as other queries in the same function).
- **WHEN** the JWT is valid and the widget+KBs are unchanged since JWT issuance **THE** auth path **SHALL** return the same `PartnerAuthContext` shape as today (no behavioural change for the non-revocation case beyond the `kb_access` values coming from DB instead of JWT).
