# SPEC-SEC-006 — Research

Codebase context supporting the Option B design for F-008.

## Finding context

**F-008** (MEDIUM, `.moai/audit/04-tenant-isolation.md`): Widget session JWTs are signed with `WIDGET_JWT_SECRET`, carry `kb_ids: list[int]` as a claim, and have a 1-hour TTL. After issuance, `_auth_via_session_token` trusts the JWT's `kb_ids` claim verbatim: `kb_access = {kb_id: "read" for kb_id in kb_ids}`. No DB cross-check occurs until the JWT naturally expires. If an admin revokes a widget's KB access in the portal, existing JWTs retain the wider scope for up to 1 hour.

Audit recommendation: either shorten TTL with refresh, cross-check `widget_kb_access` on each call, or maintain a JTI blacklist. See `.moai/audit/99-fix-roadmap.md` section SEC-006 for the three-option comparison; Option B (cross-check) is recommended there and selected here.

## Existing flow in `_auth_via_session_token`

The current function (`klai-portal/backend/app/api/partner_dependencies.py` lines 74-141) already does work that this SPEC can piggy-back on:

1. Decodes JWT, handling `ExpiredSignatureError` and `InvalidTokenError` as 401.
2. Extracts `org_id`, `wgt_id`, `kb_ids`.
3. Loads `PortalOrg` by integer id and calls `await set_tenant(db, org.id)` — the RLS tenant context is in place before any further DB work.
4. Applies a 60 rpm Redis sliding-window rate limit keyed by `wgt_id`.
5. Binds `wgt_id` and `org_id` to structlog context vars.
6. Builds `PartnerAuthContext` with `kb_access` derived from the JWT claim.

The DB cross-check fits naturally between step 3 (tenant context set) and step 4 (rate limit) — or between 4 and 6 — either placement is fine. Placing it before the rate-limit is a minor optimisation: revoked widgets do not consume a rate-limit slot. Placing it after keeps the rate-limit protection intact even if the cross-check is ever skipped.

## Widget and junction model

From `klai-portal/backend/app/models/widgets.py`:

- `Widget.id` — UUID primary key.
- `Widget.widget_id` — the public string identifier of format `wgt_<hex40>`, carried as the JWT `wgt_id` claim. Unique index `ix_widgets_widget_id`.
- `Widget.org_id` — FK to `portal_orgs.id`, indexed by `ix_widgets_org_id`.
- `WidgetKbAccess.widget_id` — FK to `Widget.id` (the UUID PK, NOT the public string id).
- `WidgetKbAccess.kb_id` — FK to `portal_knowledge_bases.id`.
- Composite PK on `(widget_id, kb_id)`; deletes cascade on widget removal.

Implication: the cross-check cannot go directly from the JWT `wgt_id` string to `WidgetKbAccess`. The string must first be resolved to the internal UUID via a `Widget` lookup. Two indexed queries, both sub-millisecond on local Postgres, both scoped by RLS to the caller's org.

## Existing query style in portal-api

The codebase already enforces `_get_{model}_or_404(id, org_id, db)` helpers for tenant-scoped lookups (see `.claude/rules/klai/projects/portal-security.md`). The widget auth path can follow the same pattern: explicit `Model.org_id == org_id` filter in the `select()`, returning 401 (not 404 — this is an auth path) when the row is missing.

## Latency and SLA context

Widget chat requests traverse portal-api with an end-to-end budget of 2-3 seconds (retrieval + LiteLLM roundtrip + SSE). Adding one indexed DB query + one indexed junction query on `Widget` + `WidgetKbAccess` is sub-millisecond in practice. `Widget` + `WidgetKbAccess` is a small table pair — a single widget typically has a handful of KBs linked. There is no risk of this query becoming a hot-path bottleneck.

The widget rate limit is already 60 rpm per widget (`_SESSION_RATE_LIMIT_RPM = 60` in the same function), so the DB-query rate is bounded at 60 per minute per active widget.

## Why Option B over A or C

- **A (shorter TTL + refresh)**: Still leaves a 5-15 minute revocation window. Requires widget-client changes, which propagate to every embedder. Does not achieve the stated success criterion of "takes effect within one chat request".
- **B (DB cross-check)**: One function body in `partner_dependencies.py`. No migrations. No client-side changes. Revocation window = DB query latency.
- **C (JTI blacklist)**: Redis would need to be written on revoke. The DB is already written on revoke (the `widget_kb_access` row is deleted) — adding Redis-write to the admin path introduces cache consistency concerns without a proportional win. Redis state is not durable across flushes; the DB is.

The trade-off is 1 extra indexed DB query per widget request, which is well within the SLA and well within DB capacity.

## Cross-references

- F-008 detail: `.moai/audit/04-tenant-isolation.md` section "F-008".
- Roadmap placement: `.moai/audit/99-fix-roadmap.md` section "SEC-006".
- Entry function: `klai-portal/backend/app/api/partner_dependencies.py` lines 74-141.
- JWT generation (unchanged): `klai-portal/backend/app/services/widget_auth.py::generate_session_token`.
- Widget-config endpoint that issues JWTs: `klai-portal/backend/app/api/partner.py::widget_config`.
- Models: `klai-portal/backend/app/models/widgets.py` (`Widget`, `WidgetKbAccess`).
- Related SPEC: `.moai/specs/SPEC-WIDGET-002/` (widget domain definition).
- Tenant-scoping pattern: `.claude/rules/klai/projects/portal-security.md`.
