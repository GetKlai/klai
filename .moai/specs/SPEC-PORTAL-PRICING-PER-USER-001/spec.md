---
id: SPEC-PORTAL-PRICING-PER-USER-001
version: "0.1.0"
status: draft-needs-sparring
created: 2026-05-12
updated: 2026-05-12
author: Mark Vletter
priority: high
supersedes:
  - SPEC-PORTAL-RBAC-001 (workspace-plan-as-feature-set assumption — see Section "What this SPEC reverses")
  - SPEC-PORTAL-PLAN-RENAME-001 (org-wide plan slug rename — collapsed by this SPEC into per-user tier derivation)
related:
  - SPEC-PORTAL-PROFILES-001 (5-rung profile ladder + PROFILE_CAPABILITIES — KEPT, becomes the single source of truth)
  - SPEC-BILLING-UPGRADE-001 (Moneybird subscription wiring — TOUCHED, line-items become per-tier)
---

# SPEC-PORTAL-PRICING-PER-USER-001: Per-user pricing derived from profile

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-12 | 0.1.0 | Initial draft. Open sparring questions in Section "Sparring required". |

---

## Summary

The Klai website ([getklai.com/pricing](https://getklai.com/pricing)) sells **per-user pricing**:

- "Klai Chat" — €28/user/month (€20 yearly)
- "Klai Chat + Knowledge" — €68/user/month (€48 yearly)

Each user is on their own pricing tier; an org bills the sum across users.

The codebase implements the **opposite**: one workspace-level plan in `portal_orgs.plan` with a profile-allowlist (`ALLOWED_PROFILES_PER_PLAN`) that restricts which profiles can be assigned on which plan. This was hardened by SPEC-PORTAL-RBAC-001 ("Per-user feature flags bestaan niet als pattern in B2B SaaS") and naming-aligned to the marketing slugs by SPEC-PORTAL-PLAN-RENAME-001 — but neither addressed the underlying mismatch.

Symptom that surfaced the gap (2026-05-12 incident): admin tried to invite a Knowledge Manager on a `professional`-plan tenant, got HTTP 403 `role_not_allowed_for_plan` because the plan's role-allowlist did not include `kb_manager`. The fix was to flip the org's plan to `knowledge`, but this is a hack — in the per-user model the act of assigning the kb_manager profile *is* the act of putting that user on the knowledge tier. There should be no separate "plan" gate.

This SPEC replaces the org-wide plan model with **profile-derived per-user tiers**:

```
user.tier = derive_tier(user.role)
org.monthly_cost = Σ tier_price(user.tier) for user in org.active_users
```

The website pricing becomes the literal billing model. The org-level `plan` field is deprecated to display-only metadata, then removed.

---

## Problem statement

Four concrete pains caused by the current (org-wide-plan + fixed-seats) model:

1. **Invite-blocked at the wrong layer (plan).** Admin assigns a profile that the role *can technically perform* (the user has the capability code-side via `PROFILE_CAPABILITIES`), but the org plan's allowlist refuses the assignment. Admin's only recourse is to upgrade the entire org to a higher plan, paying for the highest tier on every existing seat — even though only one user needs the upgraded tier.

2. **Invite-blocked at the wrong layer (seats).** Same shape as #1, different field. Admin tries to invite a 6th user on an org with `seats = 5`; gets `Seat limit reached` 4xx. Admin's only path forward is to manually bump the seat count via billing flow, which (a) blocks productivity until they do it, (b) auto-bills the new seat count for the *current plan* on every seat, even if the new user belongs on a different tier.

   Live incident 2026-05-12: admin tried to invite Linda as Group Manager on Voys; blocked by `seats = 5`, `active_users = 5`. Manual DB bump to 25 unblocked her, but the bill needs to be reconciled separately.

3. **Billing does not match the website promise.** Customer reads "€28/user/mo for Klai Chat, €68/user/mo for + Knowledge" → expects to pay €28 × 4 + €68 × 1 for an org of 4 chat users + 1 KM. Today's billing has a single `org.plan` and `org.seats` — they pay either 5 × €28 or 5 × €68 flat, not the mixed bill.

4. **Cosmetic capability mismatch.** A `kb_manager` on a `chat` plan would have the role label but not the underlying capabilities (no `kb.create_org`, no `kb.members`). The capability intersection silently strips the profile's affordances. Either the role should not be assignable (today's behavior — bad UX, see #1) or it should imply the tier upgrade (this SPEC's behavior).

---

## What this SPEC reverses

| SPEC-PORTAL-RBAC-001 claim | Why it is reversed here |
|---|---|
| "Plan = workspace features. Per-user feature flags bestaan niet als pattern in B2B SaaS." | The website explicitly sells per-user pricing. The "pattern" Klai picked from Linear / Notion is the wrong industry — those are per-seat-flat-rate. Klai's promise is per-seat-per-tier (closer to Slack workspace + Slack Pro mixed-tier). |
| `ALLOWED_PROFILES_PER_PLAN` (org-plan gates profile assignment) | Replaced by `PROFILE_TIER` (profile derives tier; tier never gates profile). |
| `PLAN_LIMITS[plan].capabilities` (plan-level capability ceiling) | Replaced by `PROFILE_CAPABILITIES[role]` directly (tier never narrows profile capabilities — tier is a billing concept, not a feature concept). |
| `derive_user_products(role, plan, enabled_addons)` | Becomes `derive_user_products(role, enabled_addons)` (no plan parameter). |

What stays:
- 5-rung profile ladder (`personal` < `company` < `kb_manager` < `group_manager` < `admin`)
- `PROFILE_CAPABILITIES`
- `enabled_addons` per org for scribe / docs (these remain workspace-level toggles)
- `FEATURE_MIN_PROFILE` floor for add-ons (e.g. scribe gates at `company`)
- Billing surface in `/admin/billing`, but with new content (tier breakdown)

---

## Solution architecture

### Profile → Tier mapping (proposed default — sparring required)

```python
# klai-portal/backend/app/core/profile_tiers.py
from enum import StrEnum

class PricingTier(StrEnum):
    FREE = "free"            # internal sentinel; trial / no billing
    CHAT = "chat"            # €28/user/mo, €20/yr
    KNOWLEDGE = "knowledge"  # €68/user/mo, €48/yr

# Source of truth: profile derives tier.
PROFILE_TIER: dict[str, PricingTier] = {
    "personal":      PricingTier.CHAT,        # individual chat user
    "company":       PricingTier.CHAT,        # org-wide chat user (no KB-management)
    "kb_manager":    PricingTier.KNOWLEDGE,   # creates / curates org KBs
    "group_manager": PricingTier.KNOWLEDGE,   # manages group memberships + group KBs
    "admin":         PricingTier.KNOWLEDGE,   # admins need full features to manage tenants
}

TIER_PRICE_MONTHLY: dict[PricingTier, int] = {
    PricingTier.FREE: 0,
    PricingTier.CHAT: 28,
    PricingTier.KNOWLEDGE: 68,
}

TIER_PRICE_YEARLY: dict[PricingTier, int] = {  # monthly equivalent on yearly contract
    PricingTier.FREE: 0,
    PricingTier.CHAT: 20,
    PricingTier.KNOWLEDGE: 48,
}

def derive_user_tier(role: str) -> PricingTier:
    """Pure function. Profile is the only input."""
    return PROFILE_TIER.get(role, PricingTier.CHAT)
```

### Capability resolution (simplified)

Before:
```python
effective_capabilities = PROFILE_CAPABILITIES[role] ∩ PLAN_LIMITS[org.plan].capabilities
```

After:
```python
effective_capabilities = PROFILE_CAPABILITIES[role]
```

The plan ceiling is gone. A user's profile fully determines what they can do, and the bill follows.

### Feature derivation (simplified)

Before:
```python
derive_user_products(role, plan, enabled_addons) -> set[str]
# user gets PLAN_FEATURES[plan] ∪ {addons gated by FEATURE_MIN_PROFILE}
```

After:
```python
derive_user_products(role, enabled_addons) -> set[str]
# user gets {chat, knowledge} (the universal product set) ∪ {addons gated by FEATURE_MIN_PROFILE}
# the per-feature *capability* still narrows what they can DO with knowledge
```

Note: every paying user gets the `chat` and `knowledge` *products* (= sidebar items / surfaces). What they can DO inside `knowledge` is controlled by `effective_capabilities` (= profile-derived). A `personal`-tier user sees the Knowledge sidebar, but cannot create org KBs (no `kb.create_org` capability). This preserves the discoverability "I can see Knowledge exists" while enforcing access via capability checks at the endpoint.

### Billing surface

`/admin/billing` becomes:

```
Klai Chat  (€28/user/mo)        4 users    €112/mo
+ Knowledge (€68/user/mo)       2 users    €136/mo
                                ─────────  ───────
                                6 users    €248/mo
```

When admin invites or promotes a user, a confirm modal shows the cost delta:
> Inviting Roman as Knowledge Manager will add €68/mo to your bill (Knowledge tier).
> Continue?

When admin demotes, a confirm modal shows the savings:
> Demoting Roman from Knowledge Manager to Personal will remove €40/mo from your bill (€68 → €28).
> Continue?

### Moneybird wiring

`MoneybirdService.update_subscription(org)` recomputes line-items as:
- Line 1: "Klai Chat — N seats" — N × €28 (or €20 yearly)
- Line 2: "+ Knowledge — M seats" — M × €68 (or €48 yearly)

`N` and `M` are derived live from `portal_users` joined with `derive_user_tier`. No `org.plan` dependency.

---

## Migration plan

Five phases. Each phase is independently shippable; a rollback at any phase leaves prod functional (degraded billing accuracy, never broken auth).

### Phase 1 — Add derivation, no behavior change (~½ day)

- Add `core/profile_tiers.py` with `PROFILE_TIER`, `derive_user_tier`, `TIER_PRICE_*`
- Add helper `compute_org_billing_breakdown(org_id) -> dict[PricingTier, int]`
- Add `/api/admin/billing/tier-breakdown` endpoint (read-only)
- Add display-only "Per-user tier breakdown" panel on `/admin/billing`
- No mutation of `portal_orgs.plan`, no removal of `ALLOWED_PROFILES_PER_PLAN`
- Tests: `derive_user_tier` exhaustive over the 5 rungs

### Phase 2 — Remove the invite blocker (~½ day)

- Remove `assert_role_allowed_for_plan` calls from `invite_user`, `update_user_role`, `promote_admin`
- Keep the function (deprecated, raise DeprecationWarning) for one release cycle so external callers (tests, migrations) get a soft signal
- Add a confirm-dialog hook on the frontend invite/promote pages: show the cost delta before the API call
- Tests: invite kb_manager on every plan tier succeeds

### Phase 3 — Decouple capabilities from plan (~½ day)

- `_derive_effective_capabilities` and `get_effective_capabilities` stop intersecting with `PLAN_LIMITS[plan].capabilities`
- `PROFILE_CAPABILITIES[role]` becomes the only input
- `PLAN_LIMITS` becomes deprecated for capability lookup; quotas (`max_personal_kbs_per_user`, `max_items_per_kb`, `can_create_org_kbs`) move to `TIER_LIMITS` keyed on tier
- Tests: `effective_capabilities("kb_manager")` returns full KB-management caps regardless of org

### Phase 4 — Per-tier Moneybird billing (~1 day)

- `MoneybirdService.update_subscription(org)` switches to per-tier line-items
- Trigger: invite-user, update-user-role, promote-admin, demote events
- One-time migration: recompute every active org's Moneybird subscription with current per-tier headcount
- Notify admins via lifecycle email if their bill changed (cost delta shown)

### Phase 5 — Deprecate `portal_orgs.plan` (~½ day)

- Mark `plan` column as `@deprecated` in `models/portal.py`
- Remove from new-org default (set to `NULL` for new orgs; existing rows untouched)
- Hide from `/admin/billing` and `/admin/settings` UI
- Document in SPEC-PORTAL-PRICING-PER-USER-001 changelog: column will be dropped in N+2 release after this lands
- Optionally: remove the `portal_orgs_plan_check` CHECK constraint (no longer enforces anything meaningful)

---

## Acceptance criteria (EARS)

**AC-1**: WHEN admin invites a user with `role=kb_manager` on any org (any current `org.plan` value), the system SHALL create the invitation successfully (no `role_not_allowed_for_plan` error).

**AC-2**: WHEN admin promotes a user from `personal` to `kb_manager`, the frontend SHALL show a confirm modal with the cost delta `+€40/mo (Knowledge tier)` before sending the API call.

**AC-3**: WHEN admin views `/admin/billing`, the page SHALL display per-tier user counts and per-tier monthly cost in a breakdown table, plus the org's total monthly cost.

**AC-4**: WHEN an admin demotes a user from `kb_manager` to `personal`, the system SHALL emit a `billing.tier_downgraded` product event with `properties.cost_delta = -40`.

**AC-5**: WHEN an admin promotes a user, the system SHALL emit a `billing.tier_upgraded` product event with the cost delta in cents.

**AC-6**: WHEN any user's effective capabilities are computed, the result SHALL equal `PROFILE_CAPABILITIES[user.role]` exactly (no plan-based narrowing).

**AC-7**: WHEN admin views `/admin/users`, each row SHALL display a "Tier" column showing the user's pricing tier (chat / knowledge), derived from their role.

**AC-8**: WHEN `MoneybirdService.update_subscription(org)` runs, it SHALL produce one line-item per active tier with the correct seat count and per-tier price.

**AC-9** (regression guard): WHEN `assert_role_allowed_for_plan` is called from any production code path after Phase 2, the system SHALL emit a `DeprecationWarning` AND return without raising — the function becomes a no-op for one release cycle, then is removed.

**AC-10**: WHEN any tenant on the legacy `portal_orgs.plan` field is migrated, the recomputed bill SHALL differ from the legacy bill by no more than the natural price-rebalance — the migration plan SHALL include an admin notification per affected tenant with the before/after cost.

**AC-11** (seat-cap removal): WHEN admin invites the (N+1)th user on an org currently at `seats = N`, the system SHALL accept the invitation, auto-record the new tier-headcount, and update the next billing-cycle invoice accordingly. NO `Seat limit reached` block. The seat count becomes a derived value (`seats = COUNT(active users)`) rather than a hard cap that must be pre-purchased.

**AC-12** (no surprise bills): WHEN admin actions raise the bill (invite, promote), the frontend SHALL show the cost delta in a confirm-modal BEFORE sending the API call. WHEN admin actions lower the bill (demote, deactivate), the frontend SHALL show the savings in a confirm-modal. NO billing change is silent.

---

## Sparring required (Mark must answer before /run)

These are open product decisions, not implementation details:

**S-1: Profile → Tier mapping confirmation**

Default proposal:
- `personal`, `company` → chat (€28)
- `kb_manager`, `group_manager`, `admin` → knowledge (€68)

Alternative ideas:
- (a) Should `admin` always be on knowledge regardless of usage? Probably yes — admins manage all features and need preview-access (current admin-bypass logic).
- (b) Should `company` get a free upgrade to knowledge if they're billed for it? Probably no — company = chat-only by definition.
- (c) Edge case: `personal` on `free` (trial) — ok, derived tier is `free` only when the org is in trial state? Or always `chat`?

**S-2: How aggressive is the Phase 4 Moneybird migration?**

- Option A (gentle): on next subscription renewal, switch to per-tier billing. Existing month-cycle continues at the legacy plan rate.
- Option B (immediate): recompute every subscription on the day Phase 4 ships, prorate the difference.
- Option C (admin-confirmed): show the new cost in the admin UI for 30 days, require admin to click "switch to per-user pricing" — only then is the Moneybird subscription updated.

**S-3: Existing `chat`-tier orgs with `kb_manager` users today — possible?**

After SPEC-PORTAL-PLAN-RENAME-001 deploy (today), allowlist enforcement means there should be ZERO `kb_manager` users on `chat` orgs. Verify with a one-off prod query before Phase 2 ships:

```sql
SELECT o.slug, COUNT(u.zitadel_user_id) AS km_count
FROM portal_orgs o
JOIN portal_users u ON u.org_id = o.id
WHERE o.plan = 'chat' AND u.role IN ('kb_manager', 'group_manager')
GROUP BY o.slug;
```

If the count is non-zero, Phase 2 has data-cleanup work in scope (decide per case: notify admin → ask if they want to upgrade the user, or keep the user but add the tier surcharge).

**S-4: What happens to `enabled_addons` (scribe, docs)?**

These remain workspace-toggles with `FEATURE_MIN_PROFILE` floors. But the billing for add-ons today is workspace-level (one toggle = scribe is on for the whole tenant). Should add-on billing also become per-user (scribe is on for these N users)? This SPEC defers that question — add-ons stay workspace-level for now.

**S-5: Naming — keep `tier` or use `seat type`?**

Industry uses both. Slack: "Pro seat", "Business+ seat". Notion: "Plus member", "Business member". Linear: "Standard seat".

Proposed: `tier` internally (in code), `Klai Chat seat` / `+ Knowledge seat` in UI strings. Confirm.

**S-6: Seat-cap behavior**

Today: `portal_orgs.seats` is a hard cap; invite-user fails with `Seat limit reached` when `COUNT(active users) >= seats`. Two paths:

- Option A (proposed in this SPEC): drop the hard cap. Seat count becomes derived (`seats = COUNT(active users)`); billing auto-rolls up. Admin sees "this invite adds €X/mo" before confirming. No invite is ever blocked by an arbitrary purchased-seats number.
- Option B: keep the cap as a safety-rail (admin can set "do not exceed N seats" to prevent runaway billing on a compromised admin account). Confirm-dialog still shows cost delta before invite, but admin must raise the cap manually.
- Option C: hybrid — soft cap with admin-configurable threshold. Below the threshold: silent auto-add. At/above: extra confirm "you're going from N to N+1, this exceeds your planned headcount of X — continue?".

Default proposal: **Option A** (no hard cap, just transparent cost on every change). Mark to confirm.

**S-7: Plan-vs-seats interaction during the transition**

Today (post SPEC-PORTAL-PLAN-RENAME-001): admin can fix invite-blocked-by-plan via `/admin/billing` plan upgrade — but that auto-bills every existing seat at the new tier. Phase 4 (per-tier Moneybird billing) closes this loophole, but until Phase 4 ships there is a window where the SPEC's behavior (auto-derive bill from headcount) is partially implemented.

Mitigation: Phases 1-3 ship the *capability + invite* changes (zero billing risk — admin can invite freely, but Moneybird still bills the legacy plan × seats). Phase 4 flips to per-tier billing per tenant on admin confirmation. No tenant is auto-migrated to a higher monthly bill without an explicit click.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Phase 4 Moneybird migration accidentally double-bills tenants | Phase 4 ships behind a feature flag `BILLING_PER_USER_ENABLED=false` per org; flip per tenant after admin confirmation |
| Removing `ALLOWED_PROFILES_PER_PLAN` triggers test failures across 30+ test files | Phase 2 is a single-PR sweep with one bulk update commit; tests asserting `role_not_allowed_for_plan` are inverted to assert success |
| `portal_orgs.plan` is referenced by external callers / SOPS env / dashboards | Phase 5 leaves the column NULL-able and untouched for one release; observability dashboards updated separately |
| Mixed-tier billing line-items break Moneybird's existing template | Test against Moneybird sandbox in Phase 4; rollback plan is to revert to single-tier billing per org for the affected period |
| Demoting a kb_manager → personal causes silent capability loss the user never agreed to | Phase 2 confirm-dialog includes the consequence: "Roman will lose access to org KBs and KB management features" |

---

## Out of scope

- Per-add-on per-user pricing (scribe-per-user, docs-per-user) — defer to a later SPEC if needed
- Annual contract enforcement / pro-ration math beyond what Moneybird already does
- Trial-tier UX (free remains an internal sentinel)
- Tier downgrade with refund handling
- Multi-currency support — assumes EUR throughout

---

## Definition of done

- All 5 phases shipped, each as its own PR
- AC-1 through AC-10 verified via tests + manual smoke on prod
- `/admin/billing` shows the per-tier breakdown for at least one mixed-tier org (e.g. a fresh test tenant)
- Moneybird invoice for the next billing cycle on the test tenant matches the per-tier breakdown
- `portal_orgs.plan` column is `@deprecated` in the model with a SPEC reference comment
- Documentation updated: `docs/runbooks/billing.md`, `docs/architecture/permissions.md`, klai-portal CLAUDE.md
- Lessons-archive: capture "spec-vs-marketing-mismatch" pitfall in `.claude/rules/klai/pitfalls/process-rules.md` so the next architectural drift is caught in design review, not on a customer-blocked invite-flow

---

Status: **draft-needs-sparring** — Mark must answer S-1 through S-5 before `/moai run`.
