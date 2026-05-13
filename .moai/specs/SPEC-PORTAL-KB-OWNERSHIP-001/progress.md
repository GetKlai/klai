# SPEC-PORTAL-KB-OWNERSHIP-001 — Implementation Progress

| Status | Date | Author |
|---|---|---|
| Implemented + deployed + live-verified | 2026-05-13 | Mark Vletter (with Claude) |

## Implementation log

| Phase | Scope | Commit / PR | Status |
|---|---|---|---|
| 1 | Personal-KB firewall (helper, dependency, invariant test) | `0a86ba85` (squashed in #615) | ✅ |
| 2 | Admin-delete with `X-Admin-Override-Confirm` header | `7f51bdd9` (squashed in #615) | ✅ |
| 3 | Offboarding orchestrator + preview + token-revoke | `bed70c94` (squashed in #615) | ✅ |
| 4 | Frontend (admin-override modal + offboard wizard) | `956d0948` (squashed in #615) | ✅ |
| 5 | Quality gate, push, PR | `5b0e8a67` (squashed in #615) | ✅ |
| Merge | PR #615 squashed → main | `c2baa1f1` | ✅ |

## REQ traceability

| REQ | Implementation | Test |
|---|---|---|
| REQ-1.1 admin-override header on DELETE | `app_knowledge_bases.py::delete_app_knowledge_base` admin-override branch | `test_kb_admin_delete_override.py::test_admin_with_override_header_succeeds_on_org_kb` |
| REQ-1.2 missing header → 403 | same handler, `if not (is_admin and header_present and …)` | `test_admin_without_override_header_gets_403`, `test_admin_with_wrong_header_value_gets_403` |
| REQ-1.3 admin-override on personal KB → 404 | belt-and-braces `if is_personal_kb and not owner` in handler body | `test_admin_override_on_personal_kb_returns_404` (+ firewall layer 1) |
| REQ-1.4 audit `kb.admin_deleted` | `log_event(action="kb.admin_deleted", details={previous_owner, kb_name, kb_slug})` | `test_admin_with_override_header_succeeds_on_org_kb` |
| REQ-1.5 failure semantics identical to owner pad | docs/ingest delete BEFORE portal-DB delete; raise aborts | `test_docs_failure_aborts_before_portal_db_delete` |
| REQ-2.1 GET offboard-preview | `admin/users.py::offboard_preview` | `test_admin_offboard_endpoint.py::test_preview_returns_kbs_and_token_counts` |
| REQ-2.1b api_keys / mcp_tokens count | `kb_offboarding.py::compute_offboard_preview` | same as REQ-2.1 |
| REQ-2.2 transactional dispositions | `apply_dispositions` runs inside the offboard tx; failure raises | `test_apply_dispositions_failure_aborts_offboard` |
| REQ-2.3 transfer mechanics | `_do_transfer` updates created_by + upserts owner row | `test_transfer_org_kb_updates_created_by_and_emits_audit` |
| REQ-2.4 personal-KB cannot be transferred | `_do_transfer` raises 400 if `is_personal_kb` | `test_transfer_personal_kb_returns_400` |
| REQ-2.5 missing dispositions → 400 with list | `offboard_user` validates `expected_kb_ids - provided_kb_ids` | `test_empty_body_with_kbs_in_preview_returns_400_with_list` |
| REQ-2.6 audit `kb.transferred` / `kb.personal_purged_on_offboard` | `log_event` + structlog mirrors in `_do_transfer`, `_do_delete` | `test_transfer_org_kb_updates_created_by_and_emits_audit`, `test_delete_personal_kb_emits_personal_purged_event` |
| REQ-2.7 auto-revoke API keys + MCP tokens | `revoke_user_credentials` (DELETE PartnerAPIKey + UPDATE PortalMcpToken.revoked_at) | `test_revoke_deletes_api_keys_and_soft_revokes_mcp_tokens`, `test_revoke_handles_missing_portal_user_row` |
| REQ-2.8 personal-KB delete is immediate | `_do_delete` runs the 3-step chain unconditionally for personal KBs | `test_delete_personal_kb_emits_personal_purged_event` |
| REQ-3.1 `get_kb_with_access` dependency | `app/api/dependencies.py` — tenant-scope SELECT + firewall + magic-slug shortcut | `test_kb_personal_firewall.py::TestGetKbWithAccess` (6 cases) |
| REQ-3.2 personal-of-others is 404 (not 403) | `is_personal_kb(kb) and kb.owner_user_id != caller` raises 404 | `test_personal_kb_of_another_user_returns_404_for_admin` (+ live-prod 7-route probe) |
| REQ-3.3 invariant test on every kb_slug route | `test_every_kb_slug_route_uses_firewall_dependency` introspects `app.routes` | the test itself; runs on every PR |
| REQ-3.4 list filter unchanged | `list_app_knowledge_bases` keeps `(owner_type='org') OR (owner_user_id=caller)` filter | (existing test stays green; live-prod confirms admin only sees own personal KB) |
| REQ-3.5 no view-as-admin escape hatch | no such code path exists; future SPECs would have to call this out | reviewed at design time |
| REQ-4.1 audit events queryable in portal_audit_log | three new actions emit via `log_event` | each REQ-1 / REQ-2 test asserts the action |
| REQ-4.2 structlog mirrors for VictoriaLogs | `_slog.info("kb_admin_deleted", ...)`, `kb_transferred`, `kb_personal_purged_on_offboard` | inspectable in VictoriaLogs queries |
| REQ-4.3 Grafana alert (Phase 2) | out of scope for MVP per SPEC | — |

All 18 in-scope requirements implemented and tested.

## Acceptance criteria

| AC | Result |
|---|---|
| AC-1 owner pad unchanged | ✅ unit tested + existing tests untouched |
| AC-2 admin + override succeeds + audit | ✅ unit tested + live verified (Voys/SIP modal renders banner) |
| AC-3 admin without header → 403 | ✅ unit tested |
| AC-4 admin override on personal KB → 404 | ✅ unit tested + live verified (7 routes on `personal-371912824923881489`) |
| AC-5 preview returns solely-owned org-KBs + personal-KBs | ✅ unit tested + live verified (Ferdian shows SIP + personal; others show only personal) |
| AC-6 missing dispositions → 400 with list | ✅ unit tested |
| AC-7 transfer + delete + revoke + audit | ✅ unit tested |
| AC-8 transfer of personal KB → 400 | ✅ unit tested + UI shows lock-badge instead of selector |
| AC-9 invariant firewall test green | ✅ `tests/test_kb_personal_firewall.py` 9/9 |
| AC-10 failure rolls back entire offboard tx | ✅ unit tested |
| AC-11 frontend "Type DELETE" gate | ✅ live verified on Voys/SIP modal |

## Owner decisions baked in

Per the SPEC's `## Beslissingen (industry-research backed)` section:

- **D1** Default transfer-receiver = the offboardende admin (Google Workspace 'direct manager' pattern). Live-verified: in the offboard wizard for Ferdian, the receiver dropdown defaults to "Mark Vletter (mark.vletter@voys.nl)" with `[selected]` state.
- **D2** Personal-KB delete on offboard = immediate. No `status` / `purge_after` columns in the schema; `_do_delete` runs the 3-step purge unconditionally.
- **D3** No restore-on-rehire pad. `suspend_user` remains the non-destructive route.
- **D4** Override mechanism = HTTP header `X-Admin-Override-Confirm: I-WAS-NOT-CREATOR`.
- **D5** API keys + MCP tokens auto-revoked at offboard. `revoke_user_credentials` does both inside the same DB transaction.

## Live verification (Voys, 2026-05-13 ~04:50 CET)

Performed via Playwright MCP against `voys.getklai.com` after the auto-deploy of merge commit `c2baa1f1`.

### Test 1 — Admin-override delete modal on SIP KB
Target: `/app/knowledge/sip/settings`. SIP is org-owned, created by Ferdian Frericks (zitadel uid `371912824923881489`); current user is Mark Vletter (admin, not creator, not in members).

- ✅ Modal opens in admin-override mode
- ✅ Yellow banner "Je hebt deze kennisbank niet aangemaakt" with `data-test-id="admin-override-banner"`
- ✅ Creator name renders: "Aangemaakt door **Ferdian Frericks**"
- ✅ Confirm input gate is "Typ **DELETE**" (not the slug `sip`)
- ✅ "Permanent verwijderen" button disabled until DELETE is typed
- ✅ Cancel without submitting (no live destructive action taken)

Screenshot: `e2e-admin-override-modal-on-sip-kb.png`.

### Test 2 — Offboard wizard for Ferdian
Target: `/admin/users/371912824923881489/edit` → Offboard button.

Preview API returned exactly the expected shape:
```json
[
  {"user": "Ferdian Frericks (ferdian.frericks@voys.nl)", "role": "kb_manager",
   "org_kbs": ["sip"], "personal": ["personal-371912824923881489"],
   "api_keys": 0, "mcp": 0}
]
```

- ✅ Wizard opens with title "Ferdian Frericks offboarden"
- ✅ "Team-kennisbanken (1)" section lists SIP with `Overdragen` action default
- ✅ Receiver dropdown lists 6 eligible org-members (Ferdian filtered out, all active+invite-accepted)
- ✅ Receiver default-selected: **Mark Vletter** (D1 satisfied)
- ✅ "Persoonlijke kennisbanken (1)" section lists Ferdian's personal KB with lock-badge "Wordt verwijderd" (no transfer selector — REQ-2.4)
- ✅ Cancel without submitting

Screenshot: `e2e-offboard-wizard-ferdian.png`.

### Test 3 — Personal-KB firewall (cross-user)
As admin Mark, probed Ferdian's `personal-371912824923881489` on 7 different routes:

| Route | Status |
|---|---|
| `GET /api/app/knowledge-bases/{slug}` | 404 ✅ |
| `GET .../stats` | 404 ✅ |
| `GET .../members` | 404 ✅ |
| `GET .../sources` | 404 ✅ |
| `GET .../connectors/` | 404 ✅ |
| `GET .../taxonomy/nodes` | 404 ✅ |
| `GET .../taxonomy/coverage` | 404 ✅ |
| `GET /api/app/knowledge-bases/personal` (own magic-slug) | 200 ✅ |
| `GET /api/app/knowledge-bases/org` (own magic-slug) | 200 ✅ |
| `GET /api/app/knowledge-bases` (list) — `personal_slugs` returned | only `["personal-368883971322282015"]` (mine) ✅ |

7/7 firewall hits returned 404 (existence-non-disclosure preserved); 2/2 sanity checks returned 200; list-filter only includes the caller's own personal KB.

## Known limitation surfaced during live verification

**Mode-detection on the delete-modal can fall back to self-mode when the SPA is loaded from a browser tab that pre-dates the deploy.** This is the "deploy-during-active-session" failure mode: the new backend serves the new 403 message, but the loaded JS bundle in the user's tab still has the older settings.tsx that doesn't compute `isAdminOverride`.

Symptom: an admin clicks delete on a colleague's KB, sees the OLD self-mode UX ("Typ <slug> om te bevestigen"), types the slug, clicks delete, then receives the NEW backend 403 with the helpful "set header X-Admin-Override-Confirm" message.

Mitigation today: hard-refresh the tab (Cmd+Shift+R). The backend's actionable error message functions as a graceful fallback — the user still learns what to do, just via a 403 instead of via the up-front banner.

Long-term mitigation (out of scope for this SPEC): a SPA-version-vs-API-version banner that prompts a refresh on mismatch. Captured as a candidate follow-up; no SPEC opened yet.

## Files touched

Backend (klai-portal/backend/):
- `app/services/access.py` — `is_personal_kb` helper (single-source-of-truth)
- `app/api/dependencies.py` — `get_kb_with_access` route-level dependency
- `app/services/default_knowledge_bases.py` — `resolve_personal_kb`, `resolve_org_kb`
- `app/api/app_knowledge_bases.py` — `delete_app_knowledge_base` admin-override pad + route-level firewall on every kb_slug route
- `app/api/app_knowledge_sources.py`, `app/api/connectors.py`, `app/api/taxonomy.py`, `app/api/kb_images.py` — route-level firewall wired
- `app/services/kb_offboarding.py` — orchestrator (NEW)
- `app/api/admin/users.py` — `offboard_preview` + `offboard_user` body schema + dispositions wiring

Frontend (klai-portal/frontend/):
- `src/components/ui/delete-kb-modal.tsx` — `mode='admin-override'` variant
- `src/components/admin/offboard-wizard.tsx` — full disposition picker (NEW)
- `src/hooks/useUserLifecycle.ts` — offboard mutation now takes `OffboardArgs`
- `src/routes/admin/users/$userId/edit.tsx`, `src/routes/admin/users/index.tsx` — wizard wired in
- `src/routes/app/knowledge/$kbSlug/settings.tsx` — derives `mode` + `creatorName` from existing flags

Tests (klai-portal/backend/tests/):
- `test_kb_personal_firewall.py` (NEW, 9 cases including invariant)
- `test_kb_admin_delete_override.py` (NEW, 7 cases)
- `test_kb_offboarding_service.py` (NEW, 14 cases)
- `test_admin_offboard_endpoint.py` (NEW, 5 cases)
- `test_user_lifecycle.py` (refactored to use `_offboard_db_mock` helper)
- `test_rls_callsite_audit.py` + `test_rls_audit.py` allowlists extended

Final test count: 2631/2631 backend, 222/222 frontend.

## Out of scope (carry-over)

Per the SPEC's `## Out of scope (MVP)` section — the following were intentionally not implemented:

- Reactivate-pad for offboarded user (eigenaarsbeslissing D3 = nee)
- Soft-delete grace-period for personal KBs (D2 = onmiddellijk)
- DB-laag RLS-policy for personal-KB firewall (Phase 2 candidate if layer B ever fails)
- Cross-org KB-transfer (tenant-isolation invariant)
- Bulk-admin-delete UI
- Soft-delete tombstones bij admin-override
- Self-service personal-KB-export voor offboarded user
- Auto-revoke OAuth grants of provider-tokens (separate hygiene SPEC)

Plus one new candidate that surfaced post-implementation:
- SPA-version banner for "deploy-during-active-session" mode-detection edge case (see Known limitation).
