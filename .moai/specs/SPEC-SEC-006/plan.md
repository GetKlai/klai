# SPEC-SEC-006 — Implementation Plan

Concrete steps to implement Option B (DB cross-check on every widget auth) for F-008.

## 1. Modify `_auth_via_session_token` in `partner_dependencies.py`

**File:** `klai-portal/backend/app/api/partner_dependencies.py` (currently lines 74-141).

**Changes, in order:**

1. Keep the existing JWT decode, `org_id` / `wgt_id` / `kb_ids` extraction, and `PortalOrg` lookup + `set_tenant` exactly as they are today.
2. **After** `set_tenant(db, org.id)`, resolve the internal `Widget` row from the JWT's `wgt_id` claim:
   - Query: `select(Widget).where(Widget.widget_id == wgt_id, Widget.org_id == org_id)` (RLS already restricts to org, but the explicit filter makes the intent obvious and defends against RLS misconfig).
   - If no row: raise the same `HTTPException(401, _AUTH_ERROR)` used elsewhere in this function.
3. Query `WidgetKbAccess` for all rows tied to this widget's internal UUID `id`:
   - Query: `select(WidgetKbAccess.kb_id).where(WidgetKbAccess.widget_id == widget.id)`.
   - Collect into a `set[int]` — call it `current_kb_ids`.
4. Intersect `kb_ids` (from JWT) with `current_kb_ids`:
   - `allowed_kb_ids = set(kb_ids) & current_kb_ids`.
5. If `allowed_kb_ids` is empty: raise `HTTPException(401, _AUTH_ERROR)` — same opaque response as other auth failures in this function.
6. Replace the current line `kb_access = {kb_id: "read" for kb_id in kb_ids}` with `kb_access = {kb_id: "read" for kb_id in allowed_kb_ids}`.
7. Rate limiting and `PartnerAuthContext` construction remain unchanged.

**Imports:** Add `from app.models.widgets import Widget, WidgetKbAccess` at the top of the file.

**Comment hygiene:** Update the existing `@MX:ANCHOR` reason comment block to note the DB cross-check and reference SPEC-SEC-006.

## 2. Resolve DB `widget.id` from JWT `wgt_id` (public id) via Widget lookup

This is an explicit step rather than a shortcut because:
- The JWT claim `wgt_id` is the **public** identifier (`widget_id` column, format `wgt_<hex40>`).
- `WidgetKbAccess.widget_id` is a FK to `Widget.id` — the **internal** UUID PK.
- Without resolving the public id to the internal UUID first, the junction query returns zero rows and every widget would fail.

No DB migration or schema change is required — both columns and their indexes already exist (`ix_widgets_widget_id` is unique; `widget_kb_access` has a composite PK).

## 3. Add one integration test

**File:** `klai-portal/backend/tests/api/test_partner_widget_auth.py` (create or extend).

**Test:** `test_widget_jwt_revocation_takes_effect_on_next_request`

**Setup:**
- Create a `PortalOrg`.
- Create a `Widget` tied to that org with a generated `widget_id`.
- Create two `WidgetKbAccess` rows for kb_id 1 and kb_id 2.
- Generate a widget session JWT via `generate_session_token(wgt_id, org_id, [1, 2], secret)`.

**Flow:**
1. Assert a widget-authenticated request (any endpoint that goes through `get_partner_key`) succeeds with the JWT.
2. Delete the `WidgetKbAccess` row for kb_id 1 (simulating admin revocation).
3. Repeat the same request specifying `kb_id=1`: expect HTTP 403 (narrowed `kb_access` no longer contains 1, downstream permission check rejects).
4. Repeat with `kb_id=2`: expect success.
5. Delete the `WidgetKbAccess` row for kb_id 2 as well.
6. Repeat the request: expect HTTP 401 (intersection empty → `_auth_via_session_token` rejects).

**Assertions:**
- The 401 error body is exactly `_AUTH_ERROR` shape (opaque — does not leak "revoked" vs "invalid").
- Structured log contains `wgt_id` binding.
- No JWT expiry wait is required — revocation takes effect immediately.

## 4. Update SPEC-WIDGET-002 cross-reference

**File:** `.moai/specs/SPEC-WIDGET-002/spec.md` (or whichever SPEC file owns widget auth semantics).

**Change:** Add a short note in the auth-behaviour section: "As of SPEC-SEC-006, widget JWT validation also cross-checks `widget_kb_access` against the DB on every request. Revoking a widget's KB access takes effect on the next request without waiting for JWT expiry."

No functional SPEC-WIDGET-002 changes — this is a documentation pointer so anyone reading the widget auth story sees the revocation guarantee.

## 5. Verification checklist

Before merging:

- [ ] `uv run ruff check klai-portal/backend/app/api/partner_dependencies.py` passes.
- [ ] `uv run --with pyright pyright klai-portal/backend/app/api/partner_dependencies.py` passes.
- [ ] New integration test passes locally.
- [ ] Existing `_auth_via_session_token` tests (expired JWT, invalid signature, missing `wgt_id`, missing `org_id`, org not found) still pass — no regression.
- [ ] Rate-limit path still exercised (widget with valid KB access still hits `check_rate_limit`).
- [ ] VictoriaLogs spot-check after staging deploy: `service:portal-api AND wgt_id:*` shows the new cross-check does not introduce error bursts.

## 6. Deployment

Standard portal-api deploy via `git push` and GitHub Action (per `klai-portal/CLAUDE.md`). No DB migration, no env var changes, no dependency changes. Rollback = revert the commit.
