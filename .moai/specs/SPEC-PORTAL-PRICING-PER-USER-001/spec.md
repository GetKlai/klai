---
id: SPEC-PORTAL-PRICING-PER-USER-001
version: "0.2.0"
status: draft-needs-sparring
created: 2026-05-12
updated: 2026-05-12
author: Mark Vletter
priority: high
supersedes:
  - SPEC-PORTAL-RBAC-001 (workspace-plan-as-feature-set assumption — superseded by per-user seat model)
  - SPEC-PORTAL-PLAN-RENAME-001 (org-wide plan slug rename — collapsed into per-user seat assignment)
related:
  - SPEC-PORTAL-PROFILES-001 (5-rung profile ladder + PROFILE_CAPABILITIES — KEPT, becomes the permission axis only)
  - SPEC-BILLING-UPGRADE-001 (Moneybird subscription wiring — TOUCHED, line-items become per-seat-type)
---

# SPEC-PORTAL-PRICING-PER-USER-001: Per-user seats, decoupled from role

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-12 | 0.1.0 | Initial draft. Proposed `PROFILE_TIER` (profile derives tier). |
| 2026-05-12 | 0.2.0 | **Architecture rewrite.** Profile-derives-tier was an anti-pattern (conflates billing with permissions — see "Why decoupled" below). Replaced with industry-standard seat+role decoupled model: each user has a `seat_type` (billing axis) AND a `role` (permission axis), assigned independently. Microsoft 365, HubSpot Seats, Salesforce, AWS Commerce/RBAC all separate these two domains. |

---

## Summary

Klai's website ([getklai.com/pricing](https://getklai.com/pricing)) sells per-user seat pricing:

- **Klai Chat** — €28/user/month (€20 yearly)
- **Klai Chat + Knowledge** — €68/user/month (€48 yearly)

Each user is on their own seat; an org bills the sum across seats.

The current codebase implements the wrong shape: one workspace-level plan (`portal_orgs.plan`) with a profile-allowlist (`ALLOWED_PROFILES_PER_PLAN`), plus a hard `portal_orgs.seats` cap. This conflates two fundamentally different domains:

1. **Billing / commerce** — what does this user cost?
2. **Permissions / RBAC** — what is this user allowed to do?

This SPEC introduces the industry-standard pattern: **two orthogonal per-user attributes**.

```
USER attributes:
  - seat_type:  chat | knowledge | viewer   (billing + feature unlock)
  - role:       personal | company | kb_manager | group_manager | admin   (permissions within unlocked features)

These are assigned independently. The combination produces the user experience.

effective_features      = SEAT_FEATURES[seat_type]                          # what this seat unlocks
effective_capabilities  = PROFILE_CAPABILITIES[role] ∩ SEAT_FEATURES[seat]  # permissions within unlocked features
monthly_bill            = Σ SEAT_PRICE[user.seat_type] for user in active   # cost rolls up from seats
```

---

## Why decoupled (industry research, see Section "Sources")

Three real-world patterns, all consistently decouple billing from RBAC:

**Microsoft 365** ([docs](https://learn.microsoft.com/en-us/microsoft-365/admin/manage/assign-licenses-to-users)):
> "Assigning a license determines what services a user can access, while assigning a role determines what administrative permissions a user has. The permissions to manage licenses are separate from the licenses themselves."
>
> A Global Admin can be on a Business Basic license. The admin role grants management powers; it does not grant Premium features.

**HubSpot Seats Model** ([docs](https://knowledge.hubspot.com/account-management/manage-seats)):
> Core Seat / Sales Seat / Service Seat / View-Only Seat. Seat type is determined by the hub access needed, NOT tied to the user's role.

**AWS / Azure governance** ([Microsoft Tech Community on role anti-patterns](https://techcommunity.microsoft.com/blog/startupsatmicrosoftblog/role-structures-anti-patterns-and-the-10-governance-principles/4510070)):
> "Identity, billing, and resource deployment are fundamentally different domains that must be operated and secured differently. Conflating billing tier with RBAC is an anti-pattern."

The v0.1.0 of this SPEC violated this principle — `PROFILE_TIER` proposed deriving the billing tier from the role. v0.2.0 fixes that.

---

## Problem statement

Four concrete pains caused by the current (org-wide-plan + fixed-seats + role-allowlist) model:

1. **Invite-blocked at the wrong layer (plan).** Admin assigns a role that the user *can technically perform* (the role has the capability code-side via `PROFILE_CAPABILITIES`), but the org plan's allowlist refuses the assignment. Admin's only recourse is to upgrade the entire org, paying the highest tier on every seat — even though only one user needs the upgrade.
   - **Live incident 2026-05-12**: Mark blocked from inviting Roman as Knowledge Manager on Voys (`professional` plan, `kb_manager` not in allowlist). Manual fix: `UPDATE portal_orgs SET plan = 'knowledge' WHERE slug = 'voys'`.

2. **Invite-blocked at the wrong layer (seats).** Admin tries to invite a 6th user on an org with `seats = 5`; gets `Seat limit reached`. Admin must manually bump the seat count via billing flow, which auto-bills the new count at the *current plan tier* on every seat.
   - **Live incident 2026-05-12**: Mark blocked from inviting Linda as Group Manager on Voys (5/5 seats). Manual fix: `UPDATE portal_orgs SET seats = 25 WHERE slug = 'voys'`.

3. **Billing does not match the website promise.** Customer reads "€28/user/mo for Klai Chat, €68/user/mo for + Knowledge" → expects to pay €28 × 4 + €68 × 1 for an org of 4 chat users + 1 KM. Today's billing has `org.plan` and `org.seats` only — they pay flat (5 × €28 OR 5 × €68), not the mixed bill.

4. **Cosmetic capability mismatch.** A `kb_manager` role on a `chat` plan would have the role label but not the underlying features (no `kb.create_org`, no `kb.members`). Today the plan allowlist refuses the assignment (#1). In the seat+role model, the assignment is allowed, the seat constraint shows in the UI, and the admin can fix it cleanly: either upgrade Roman's seat to Knowledge, or accept that he has the title but limited features.

---

## Solution architecture (v0.2.0)

### Two orthogonal user attributes

**Attribute 1 — `seat_type`** (billing + feature unlock)

```python
# klai-portal/backend/app/core/seats.py
from enum import StrEnum

class SeatType(StrEnum):
    VIEWER = "viewer"          # €0/mo. Read-only. For stakeholders / leads.
    CHAT = "chat"              # €28/mo, €20/yr. Klai Chat tier.
    KNOWLEDGE = "knowledge"    # €68/mo, €48/yr. Klai Chat + Knowledge tier.

# What features each seat type unlocks (workspace-product surface).
SEAT_FEATURES: dict[SeatType, frozenset[str]] = {
    SeatType.VIEWER: frozenset({"chat_readonly", "knowledge_readonly"}),
    SeatType.CHAT: frozenset({
        "chat",
        "knowledge.basic",         # personal KBs, 5/20 quota
        "kb.connectors",
    }),
    SeatType.KNOWLEDGE: frozenset({
        "chat",
        "knowledge.basic",
        "knowledge.full",          # unlimited KBs, org KBs
        "kb.connectors",
        "kb.connectors.external",  # GitHub, Notion, Google Drive, SharePoint
        "kb.create_org",
        "kb.members",
        "kb.taxonomy",
        "kb.gaps",
    }),
}

SEAT_PRICE_MONTHLY: dict[SeatType, int] = {
    SeatType.VIEWER: 0,
    SeatType.CHAT: 28,
    SeatType.KNOWLEDGE: 68,
}

SEAT_PRICE_YEARLY_MONTH_EQUIV: dict[SeatType, int] = {
    SeatType.VIEWER: 0,
    SeatType.CHAT: 20,
    SeatType.KNOWLEDGE: 48,
}
```

**Attribute 2 — `role`** (permissions, unchanged from SPEC-PORTAL-PROFILES-001)

```python
# 5-rung profile ladder STAYS as-is.
PROFILE_LADDER = ["personal", "company", "kb_manager", "group_manager", "admin"]
```

### Effective access composition

The user's effective access is the **intersection** of what their seat unlocks and what their role permits:

```python
def effective_features(seat_type: SeatType) -> frozenset[str]:
    """What product surfaces does this user see? Determined by seat alone."""
    return SEAT_FEATURES[seat_type]

def effective_capabilities(role: str, seat_type: SeatType) -> frozenset[Capability]:
    """What permissions does the user have within unlocked features?

    Intersect role-granted capabilities with seat-unlocked features.
    A kb_manager role on a chat seat gets KM permissions in code, but the
    UI only surfaces them where the seat unlocks the underlying feature.
    """
    role_caps = PROFILE_CAPABILITIES[role]
    seat_unlocked = SEAT_FEATURES[seat_type]
    # Capability is granted iff (a) the role has it AND (b) the seat unlocks
    # the product surface where the capability applies.
    return frozenset(c for c in role_caps if _capability_unlocked_by_seat(c, seat_unlocked))

def monthly_bill(org_id: int) -> int:
    """Per-tier headcount × per-tier price, summed."""
    return sum(SEAT_PRICE_MONTHLY[u.seat_type] for u in active_users(org_id))
```

### What changes vs. today

| Concept | Today | After this SPEC |
|---|---|---|
| `portal_orgs.plan` | Workspace tier (`free`/`chat`/`knowledge`) | **Deprecated.** Becomes display-only, then dropped. |
| `portal_orgs.seats` | Hard cap on user count | **Deprecated.** Headcount is derived from active users. |
| `portal_users.seat_type` | Does not exist | **New column.** Per-user billing axis. |
| `portal_users.role` | Per-user permissions | **Unchanged.** Per-user permission axis. |
| `ALLOWED_PROFILES_PER_PLAN` | Gate on role assignment | **Removed.** Profile is always assignable. |
| Capability resolution | `PROFILE_CAPABILITIES[role] ∩ PLAN_LIMITS[org.plan].capabilities` | `PROFILE_CAPABILITIES[role]` filtered through `SEAT_FEATURES[user.seat_type]` |
| `enabled_addons` (scribe/docs) | Workspace toggle | **Unchanged.** Workspace toggle, with profile floor (FEATURE_MIN_PROFILE). |
| Billing line-items | `plan × seats` flat | Per-seat-type line-items: `chat: N × €28 + knowledge: M × €68 + viewer: K × €0` |

### Smart defaults in admin UI

Admin still gets a one-click experience for the common case. When inviting / promoting a user, the UI **suggests** a seat type based on the chosen role, but the admin can override:

```
┌─────────────────────────────────────────────────────────┐
│  Invite user                                            │
├─────────────────────────────────────────────────────────┤
│  Email:      roman.kamin@voys.nl                        │
│  Role:       [Knowledge Manager      ▼]                 │
│  Seat:       (●) Knowledge   €68/mo  ← suggested        │
│              ( ) Chat        €28/mo                     │
│              ( ) Viewer      €0/mo                      │
│                                                         │
│  ⚠ Knowledge Manager + Chat seat: Roman would have      │
│    KM permissions but the Chat seat doesn't unlock      │
│    org KB management. He'd see a limited UI.            │
│                                                         │
│  Cost impact: +€68/mo (Knowledge tier)                  │
│              [Cancel]  [Send invitation]                │
└─────────────────────────────────────────────────────────┘
```

The ⚠ warning surfaces when the role has capabilities the seat doesn't unlock, but the invite is still allowed — admin choice.

### Default seat suggestion rules

```python
DEFAULT_SEAT_FOR_ROLE: dict[str, SeatType] = {
    "personal":      SeatType.CHAT,
    "company":       SeatType.CHAT,
    "kb_manager":    SeatType.KNOWLEDGE,
    "group_manager": SeatType.KNOWLEDGE,
    "admin":         SeatType.KNOWLEDGE,
}

def suggest_seat(role: str) -> SeatType:
    return DEFAULT_SEAT_FOR_ROLE.get(role, SeatType.CHAT)
```

This keeps the SMB single-click experience identical to v0.1.0's profile-derives-tier, while letting power-users decouple when needed (e.g. a `Viewer seat + admin role` for a board member who needs to see metrics but not be charged €68/mo).

### Add-ons (scribe, docs) stay workspace-level

Per-add-on per-user pricing is out of scope. `enabled_addons` remains an org-wide toggle with a `FEATURE_MIN_PROFILE` floor, exactly as today. A future SPEC may revisit if needed.

---

## Migration plan

Six phases. Each is independently shippable; rollback at any phase leaves prod functional.

### Phase 1 — Add seat_type column + read-only billing breakdown (~1 day)

- Add `seats.py` module with `SeatType`, `SEAT_FEATURES`, prices, helpers
- Alembic migration: add `portal_users.seat_type` column with `NOT NULL DEFAULT` derived from current role:
  ```sql
  ALTER TABLE portal_users ADD COLUMN seat_type TEXT;
  UPDATE portal_users SET seat_type = CASE
      WHEN role IN ('personal', 'company') THEN 'chat'
      WHEN role IN ('kb_manager', 'group_manager', 'admin') THEN 'knowledge'
      ELSE 'chat'
  END;
  ALTER TABLE portal_users ALTER COLUMN seat_type SET NOT NULL;
  ALTER TABLE portal_users ADD CONSTRAINT portal_users_seat_type_check
      CHECK (seat_type IN ('viewer', 'chat', 'knowledge'));
  ```
  This default keeps every existing user with their current effective access — `kb_manager` users keep their KM features (Knowledge seat), `personal` users keep theirs (Chat seat). Zero behavior change at this phase.
- Add `/api/admin/billing/breakdown` endpoint returning `{seat_type: count}`
- Add display-only "Per-seat breakdown" panel on `/admin/billing`
- Tests: seat_type defaults derived correctly, breakdown endpoint returns correct counts

### Phase 2 — Seat assignment as first-class admin UI element (~1 day)

- Frontend: invite/edit user modal shows seat selector with smart-default + cost-delta + ⚠ mismatch warning
- New endpoint `PATCH /api/admin/users/{user_id}/seat` to change seat without changing role
- Backend audit-log emits `user.seat_changed` event with old/new seat and cost delta
- Tests: admin can change seat independently of role; ⚠ warning surfaces when role+seat mismatch

### Phase 3 — Remove the invite/role blockers (~½ day)

- Remove `assert_role_allowed_for_plan` calls from `invite_user`, `update_user_role`, `promote_admin`
- Keep the function as deprecated no-op for one release cycle (DeprecationWarning)
- Remove the `seats` hard-cap check from `invite_user`
- Tests: invite kb_manager on any org succeeds; invite N+1th user on a `seats=N` org succeeds

### Phase 4 — Decouple capabilities from plan, intersect with seat (~1 day)

- `_derive_effective_capabilities(role, seat_type)` replaces `_derive_effective_capabilities(role, plan)`
- `effective_capabilities` = `PROFILE_CAPABILITIES[role] ∩ {capabilities unlocked by SEAT_FEATURES[seat_type]}`
- `PLAN_LIMITS` deprecated for capability lookup; quotas (`max_personal_kbs_per_user`, `max_items_per_kb`, `can_create_org_kbs`) move into `SEAT_FEATURES` derivation
- Tests: `kb_manager + chat seat` returns capabilities only for chat-seat-unlocked features; `kb_manager + knowledge seat` returns full KM cap-set

### Phase 5 — Per-seat-type Moneybird billing (~1.5 days)

- `MoneybirdService.update_subscription(org)` switches to per-seat-type line-items
- Live recompute from `portal_users` joined with `seat_type` (no `org.plan` dependency)
- Trigger: invite-user, update-user-role (if seat changes via smart-default), patch-seat
- One-time per-org migration: feature-flagged `BILLING_PER_SEAT_ENABLED` per `portal_orgs.id`. Phase 5 ships with the flag OFF for all existing tenants. Each tenant's admin sees a "switch to per-user billing" CTA on `/admin/billing` with before/after cost. Only after admin confirmation does the Moneybird subscription update.
- Lifecycle email on bill change with cost delta breakdown

### Phase 6 — Deprecate `portal_orgs.plan` and `portal_orgs.seats` (~½ day)

- Mark both columns `@deprecated` in `models/portal.py`
- Hide from admin UI (settings, billing)
- Remove `portal_orgs_plan_check` constraint (no longer enforces anything meaningful)
- Schedule column drop for N+2 release after Phase 5 completes
- Document in changelog: "These columns are display-only; source of truth is per-user seat_type"

---

## Acceptance criteria (EARS)

**AC-1**: WHEN admin invites a user with `role=kb_manager` on any org, the system SHALL create the invitation successfully (no `role_not_allowed_for_plan` error). The system SHALL also assign a default seat (`knowledge` per `DEFAULT_SEAT_FOR_ROLE`) unless admin overrode it.

**AC-2**: WHEN admin invites the (N+1)th user on an org with `seats = N`, the system SHALL accept the invitation. The bill SHALL roll up from `COUNT(active users)` per seat type, not from `org.seats`.

**AC-3**: WHEN admin invites or promotes a user, the frontend SHALL show a confirm modal with the cost delta (`+€X/mo (seat tier)`) and the seat selection BEFORE sending the API call. NO billing change is silent.

**AC-4**: WHEN admin demotes a user OR changes their seat to a lower tier, the frontend SHALL show a confirm modal with the savings AND the consequence ("Roman will lose access to org KB management").

**AC-5**: WHEN admin assigns a `role=kb_manager` with `seat_type=chat`, the system SHALL accept the assignment AND surface a ⚠ warning in the admin UI: "Knowledge Manager role + Chat seat: this user has KM permissions but the Chat seat doesn't unlock org KB management."

**AC-6**: WHEN any user's effective capabilities are computed, the result SHALL equal `PROFILE_CAPABILITIES[role]` filtered through `SEAT_FEATURES[seat_type]`. NO plan-based intersection.

**AC-7**: WHEN admin views `/admin/billing`, the page SHALL display per-seat-type user counts AND per-seat-type monthly cost AND the org's total monthly cost.

**AC-8**: WHEN admin views `/admin/users`, each row SHALL display two columns: "Role" and "Seat" (each with the current value, both editable).

**AC-9**: WHEN `MoneybirdService.update_subscription(org)` runs (Phase 5+), it SHALL produce one line-item per active seat type with the correct headcount and per-seat price.

**AC-10**: WHEN a `viewer` seat is assigned, the user SHALL have read-only access to chat + knowledge surfaces AND incur €0 billing impact AND NOT count against any per-seat license cap (because there is none after Phase 3).

**AC-11**: WHEN `assert_role_allowed_for_plan` is called from any production code path after Phase 3, the system SHALL emit a `DeprecationWarning` AND return without raising — no-op for one release cycle, then removed.

**AC-12**: WHEN any tenant's billing is migrated to per-seat (Phase 5), the migration SHALL only happen after admin click on `/admin/billing` "switch to per-user billing" CTA. Until clicked, the tenant remains on legacy `plan × seats` billing. NO automatic per-tenant billing change.

**AC-13** (regression guard): WHEN any future code path tries to derive the seat type from the role automatically (the v0.1.0 anti-pattern), CI SHALL fail. An ast-grep rule under `rules/no-profile-derives-seat.yml` SHALL detect direct `seat = SEAT_FOR_ROLE[role]` assignments outside the explicit `suggest_seat()` helper.

---

## Sparring required (Mark must answer before /run)

**S-1: Seat types — confirm three or expand?**

Default proposal: `viewer` (€0), `chat` (€28), `knowledge` (€68) — matches the website + adds a free read-only tier (a common ask in B2B for stakeholders/leads).

Alternatives to consider:
- (a) Skip `viewer` for now (only paid seats; revisit later) — simpler, fewer states to test
- (b) Add a `power` seat above knowledge for future product expansion (not on website yet) — premature

Recommend (a) if you want minimal scope, default proposal (with viewer) if you want the read-only-stakeholder pitch ready.

**S-2: Default seat per role mapping**

Default proposal:
- `personal`, `company` → chat
- `kb_manager`, `group_manager`, `admin` → knowledge

Confirm. The smart-default suggestion follows this; admin can always override.

**S-3: How aggressive is Phase 5 Moneybird migration?**

This SPEC proposes **Option C** (admin-confirmed per tenant). No tenant gets a different bill without an explicit admin click on `/admin/billing`. This is the safest default and aligns with `AC-12`.

Alternatives:
- Option A (gentle): on next renewal, switch automatically — risk of surprise bill on renewal day
- Option B (immediate): recompute everything Day 1 of Phase 5 — risk of mass billing-shock complaints

Recommend Option C. Confirm.

**S-4: ⚠ Warning vs hard block on role+seat mismatch**

Default proposal: warning only (allow the mismatch, surface the consequence in the UI). Microsoft 365 / HubSpot pattern.

Alternative: hard-block certain combos (e.g. `kb_manager` MUST have `knowledge` or `viewer` seat — not `chat`). Stricter, less flexible.

Recommend warning-only. Confirm.

**S-5: What about `enabled_addons` (scribe, docs)?**

Workspace-level toggles stay as today, with `FEATURE_MIN_PROFILE` floor. Per-add-on per-user pricing deferred to a later SPEC.

Confirm or push back if you want add-ons in this SPEC's scope.

**S-6: Naming — seat type vs license vs subscription?**

Industry uses all three. Microsoft: "license". HubSpot: "seat". Salesforce: "user license" + "permission set" (separate concepts).

Default proposal: `seat_type` internally (in code/DB/API), "Klai Chat seat" / "Knowledge seat" / "Viewer seat" in UI strings.

Confirm.

**S-7: Phase ordering**

The 6-phase ordering above:
1. Add column + breakdown (read-only)
2. Admin UI for seat assignment
3. Remove blockers (allowlist + seat cap)
4. Decouple capabilities
5. Per-seat Moneybird billing (admin-confirmed)
6. Deprecate `portal_orgs.plan` / `seats`

Phases 1-4 ship without billing impact (legacy Moneybird billing continues unchanged through Phase 4). Phase 5 introduces real billing change behind a per-tenant flag. This means: the customer-facing pain (invite blocks, surprise upgrades) is resolved by Phase 3, while billing accuracy follows in Phase 5 on opt-in basis.

Confirm or propose re-ordering.

**S-8: Existing chat-tier orgs with kb_managers (data audit)**

Pre-flight query before Phase 1:
```sql
SELECT o.slug, COUNT(u.zitadel_user_id) AS km_count
FROM portal_orgs o
JOIN portal_users u ON u.org_id = o.id
WHERE o.plan = 'chat' AND u.role IN ('kb_manager', 'group_manager')
GROUP BY o.slug;
```
After SPEC-PORTAL-PLAN-RENAME-001 deploy (today), allowlist enforcement should mean ZERO rows. If non-zero: those users default to `knowledge` seat in Phase 1 migration (matches their role's expected access), but admin gets a one-time email noting the seat assignment and any billing impact.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Phase 5 Moneybird migration accidentally double-bills tenants | Per-tenant feature flag `BILLING_PER_SEAT_ENABLED`, OFF by default. Admin click required. |
| Removing `ALLOWED_PROFILES_PER_PLAN` triggers test failures across 30+ test files | Phase 3 single-PR sweep with one bulk-update commit (mechanical, like SPEC-PORTAL-PLAN-RENAME-001). Tests asserting `role_not_allowed_for_plan` are inverted to assert success. |
| `portal_orgs.plan` is referenced by external callers / SOPS env / dashboards | Phase 6 leaves the column NULL-able and untouched for one release. Observability dashboards updated separately. |
| Mixed-seat billing line-items break Moneybird's existing template | Test against Moneybird sandbox in Phase 5 (rolls into S-3 confirm). Rollback plan: revert to legacy single-tier billing per affected tenant. |
| Admin demotes user OR lowers seat → silent capability loss the user never agreed to | Phase 2 confirm-modal includes consequence ("Roman will lose access to org KBs and KB management features") and emits a `user.seat_changed` audit event. |
| `viewer` seat triggers free-tier abuse (org creates 1000 viewer seats) | Defer abuse-rate-limiting to a later SPEC if observed in practice. v0.2.0 trusts admins. |
| Profile-derives-seat anti-pattern creeps back via "convenient" code | AC-13 ast-grep CI rule blocks direct `SEAT_FOR_ROLE[role]` outside the explicit `suggest_seat()` helper. |

---

## Out of scope

- Per-add-on per-user pricing (scribe-per-user, docs-per-user) — defer to a later SPEC if customer demand surfaces
- Annual contract enforcement / pro-ration math beyond what Moneybird already does
- Trial-tier UX (free remains an internal sentinel; viewer is not a trial)
- Tier downgrade with refund handling — Moneybird's standard pro-ration applies
- Multi-currency support — assumes EUR throughout
- Per-user-per-seat granular feature toggles beyond what `SEAT_FEATURES` exposes (this SPEC keeps `SEAT_FEATURES` as a fixed table; if Klai needs per-tenant overrides, that's a follow-up SPEC)

---

## Definition of done

- All 6 phases shipped, each as its own PR
- AC-1 through AC-13 verified via tests + manual smoke on prod
- `/admin/billing` shows the per-seat breakdown for at least one mixed-seat org (e.g. a fresh test tenant with a chat user + knowledge user + viewer user)
- Moneybird invoice for the next billing cycle on the test tenant matches the per-seat breakdown
- `portal_orgs.plan` and `portal_orgs.seats` columns are `@deprecated` in the model with a SPEC reference comment
- Documentation updated: `docs/runbooks/billing.md`, `docs/architecture/permissions.md`, klai-portal CLAUDE.md
- New rule captured: `.claude/rules/klai/pitfalls/process-rules.md` adds `profile-derives-seat-anti-pattern` describing v0.1.0's wrong turn so the next time someone proposes "let me just derive the billing tier from the role", the answer cites Microsoft 365 / HubSpot / AWS pattern

---

## Sources (industry research that drove v0.2.0)

The decoupled seat+role architecture is consistent across major B2B SaaS platforms:

- [Schematic — Seat-based Pricing 101: The classic SaaS model](https://schematichq.com/blog/seat-based-pricing-101-the-classic-saas-model-that-still-works-sometimes)
- [Microsoft Learn — Assign or unassign licenses for users](https://learn.microsoft.com/en-us/microsoft-365/admin/manage/assign-licenses-to-users?view=o365-worldwide) — license vs role explicitly separate
- [Microsoft Learn — About administrator roles in M365](https://learn.microsoft.com/en-us/microsoft-365/admin/add-users/about-admin-roles?view=o365-worldwide) — role grants permissions, not features
- [HubSpot — Assign and manage seats](https://knowledge.hubspot.com/account-management/manage-seats) — Core / Sales / Service / View-Only seats, all role-independent
- [Microsoft Tech Community — Role Structures, Anti-Patterns, 10 Governance Principles](https://techcommunity.microsoft.com/blog/startupsatmicrosoftblog/role-structures-anti-patterns-and-the-10-governance-principles/4510070) — explicit Identity/RBAC/Commerce three-plane model
- [AWS — SaaS Lens: General design principles](https://docs.aws.amazon.com/wellarchitected/latest/saas-lens/general-design-principles.html) — separation of concerns across billing/identity/resources
- [Orb — Billing system architecture for SaaS 101](https://www.withorb.com/blog/billing-architecture)

---

Status: **draft-needs-sparring** — Mark must answer S-1 through S-8 before `/moai run`.
