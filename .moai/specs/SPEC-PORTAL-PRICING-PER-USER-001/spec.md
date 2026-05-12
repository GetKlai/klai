---
id: SPEC-PORTAL-PRICING-PER-USER-001
version: "0.4.0"
status: ready-for-run
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
| 2026-05-12 | 0.2.0 | **Architecture rewrite.** Profile-derives-tier was an anti-pattern (conflates billing with permissions). Replaced with industry-standard seat+role decoupled model. |
| 2026-05-12 | 0.3.0 | **Sparring resolved + gap-fixes.** S-1 through S-8 answered (see decisions below). Add explicit `CAPABILITY_TO_SEAT_FEATURE` mapping table. Replace workspace-`enabled_addons` model with seat-included scribe/docs (gated by `FEATURE_MIN_PROFILE` company-floor). Add `portal_user_seat_history` for prorated billing audit. Write actual ast-grep rule body for AC-13. Reframed "industry-standard" claim (decoupled is right *for Klai* because the website promises per-user pricing — Linear/Notion's workspace-uniform is also valid for that audience). Status flipped to `ready-for-run`. |
| 2026-05-12 | 0.4.0 | **Adversarial pre-run review.** 8 findings addressed before Phase 1 implementation. (1) Viewer-seat capability semantics made explicit — viewer is billing-only; read-only access is enforced by `effective_features` rendering on the FE + absence of write capabilities on the BE; no `chat_readonly` / `knowledge_readonly` capability layer added in Phase 1 because backfill produces zero viewer-users (Phase 2 adds explicit `chat.send` / `kb.write` capabilities). (2) AC-13 ast-grep rule rewritten — dict-literal pattern dropped (ast-grep's order-dependent dict-key match is brittle); replaced with assignment + mapping-call patterns + a required `rules/tests/` positive/negative fixture set that CI runs alongside the rule; `alembic/**` added to `files.exclude`. (3) History append moved from SQLAlchemy event-listener to a Postgres `BEFORE UPDATE OR INSERT` trigger PLUS a partial `UNIQUE INDEX (user_id) WHERE valid_to IS NULL` — immune to `session.execute(update(...))` ORM-bypass and to concurrent-update race. (4) RLS policy added on `portal_user_seat_history` (Cat-D pattern, schema-qualified `billing._rls_current_org_id()` helper per `postgres-no-return-type-overload` pitfall). (5) `status TEXT NOT NULL` snapshot column added to the history table so the Phase 5 prorate query can scope to billable periods (a `deactivated` row closes the user's billable window cleanly). (6) Phase 1 migration step "DROP COLUMN enabled_addons" removed — the column was already dropped by SPEC-PORTAL-EXTENSIONS-UNIFY-001 (`e0ad7c2b1e80`, merged 2026-05-12); the Phase 1 alembic migration would have crashed on `column does not exist`. (7) `effective_features` uses `.get(role, -1)` for unknown-role safety, mirroring the FEATURE_MIN_PROFILE lookup pattern. (8) AC-7 endpoint contract makes `require_at_least("admin")` RBAC explicit on `GET /api/admin/billing/breakdown`. Status stays `ready-for-run`. |

### Sparring resolved (v0.3.0)

| # | Question | Decision |
|---|---|---|
| 1 | Three seat types or skip viewer | Three (viewer/chat/knowledge). Mark: "doe nou normaal — als ze toch betalen, maakt gratis viewer niet uit." |
| 2 | Default seat per role | personal/company → chat; kb_manager/group_manager/admin → knowledge. |
| 3 | Phase 5 Moneybird migration | Admin-confirmed per tenant. No surprise bills. |
| 4 | Role+seat mismatch | Warning only, allow combo (Microsoft 365 / HubSpot pattern). |
| 5 | Add-ons (scribe/docs) workspace-level? | NO — replace workspace `enabled_addons` toggle with seat-included scribe/docs. Both seats (chat + knowledge) include scribe + docs. The `company` role-floor in `FEATURE_MIN_PROFILE` keeps personal-role users out (Mark: "vanaf bedrijfschat"). |
| 6 | Naming | `seat_type` internal, "Klai Chat seat" / "Knowledge seat" / "Viewer seat" in UI. |
| 7 | Phase ordering | Confirmed. Customer pain resolved at Phase 3, billing accuracy at Phase 5 opt-in. |
| 8 | User-history tracking | Today: `created_at` + current `status` only — no from-to dates per seat assignment. Add `portal_user_seat_history` table in Phase 1 so Phase 5 can prorate accurately. |

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

## Why decoupled — for Klai (industry research, see Section "Sources")

Two SaaS pricing patterns are valid and battle-tested:

**Pattern A — workspace-uniform tier**: every member of an org pays the same per-seat rate. Used by Linear, Notion, Slack (within a workspace). Simpler architecture, single billing axis, one plan per org. SPEC-PORTAL-RBAC-001 chose this and is internally consistent.

**Pattern B — decoupled seat + role**: each user has an individually-assigned seat (billing axis) AND a role (permission axis). Used by Microsoft 365, HubSpot, Salesforce, Google Workspace. More flexible, two axes, mixed-tier orgs.

**Why this SPEC picks Pattern B for Klai specifically**: the website ([getklai.com/pricing](https://getklai.com/pricing)) sells per-user pricing — €28/user, €68/user. That language commits Klai to mixed-tier orgs. Pattern A's flat-tier billing would either over-charge (everyone at the highest tier) or under-charge (everyone at the lowest tier). Neither matches what a customer reads on the marketing page.

This is a reading of *Klai's pricing promise*, not an architectural absolute. If Klai ever drops per-user pricing in favor of flat workspace pricing, Pattern A becomes correct again.

Pattern B's industry references confirm the *shape* of the decoupling once you commit to per-user pricing:

**Microsoft 365** ([docs](https://learn.microsoft.com/en-us/microsoft-365/admin/manage/assign-licenses-to-users)):
> "Assigning a license determines what services a user can access, while assigning a role determines what administrative permissions a user has. The permissions to manage licenses are separate from the licenses themselves."

**HubSpot Seats Model** ([docs](https://knowledge.hubspot.com/account-management/manage-seats)):
> Core Seat / Sales Seat / Service Seat / View-Only Seat. Seat type is determined by hub access needed, NOT tied to role.

**Microsoft governance anti-patterns** ([techcommunity](https://techcommunity.microsoft.com/blog/startupsatmicrosoftblog/role-structures-anti-patterns-and-the-10-governance-principles/4510070)):
> "Identity, billing, and resource deployment are fundamentally different domains. Conflating billing tier with RBAC is an anti-pattern."

The v0.1.0 of this SPEC chose Pattern B but implemented it as `PROFILE_TIER` (profile derives tier) — collapsing the two axes back into one. v0.2.0+ keeps them properly orthogonal.

---

## Problem statement

Four concrete pains caused by the current (org-wide-plan + fixed-seats + role-allowlist) model:

1. **Invite-blocked at the wrong layer (plan).** Admin assigns a role that the user *can technically perform* (the role has the capability code-side via `PROFILE_CAPABILITIES`), but the org plan's allowlist refuses the assignment. Admin's only recourse is to upgrade the entire org, paying the highest tier on every seat — even though only one user needs the upgrade.
   - **Surfaced 2026-05-12**: customer admin attempted to invite a Knowledge Manager on a `professional`-plan tenant; HTTP 403 `role_not_allowed_for_plan`. Resolved with a one-row DB update that flipped the org plan to `knowledge` — a workaround, not a fix.

2. **Invite-blocked at the wrong layer (seats).** Admin tries to invite a 6th user on an org with `seats = 5`; gets `Seat limit reached`. Admin must manually bump the seat count via billing flow, which auto-bills the new count at the *current plan tier* on every seat.
   - **Surfaced 2026-05-12**: same tenant, second invite attempt for a Group Manager; blocked at the seat cap (5/5). Resolved with another one-row DB update bumping seats to 25 — same workaround pattern, different field.

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
# scribe + docs are INCLUDED in chat and knowledge seats (no add-on toggle).
# Role-floor (FEATURE_MIN_PROFILE) keeps personal-role users out: scribe/docs
# require role >= company. Mark v0.3.0: "scribe is voor iedereen vanaf
# Bedrijfschat, net als docs."
# v0.4.0: viewer is billing-only. `chat_readonly` / `knowledge_readonly` are
# FE-rendering hints (sidebar shows the surfaces in read-only mode), NOT
# capabilities. Backend write-endpoints enforce capability requirements
# (e.g. `chat.send`, `kb.write`); a viewer has none of those, so endpoint-
# level access falls back to 403 even if the FE were bypassed. Phase 1
# backfill produces zero viewer-users — adding explicit read capabilities
# is deferred to Phase 2 (seat-selector arrival).
SEAT_FEATURES: dict[SeatType, frozenset[str]] = {
    SeatType.VIEWER: frozenset({"chat_readonly", "knowledge_readonly"}),
    SeatType.CHAT: frozenset({
        "chat",
        "knowledge.basic",         # personal KBs, 5/20 quota
        "kb.connectors",
        "scribe",                  # available; gated by role >= company
        "docs",                    # available; gated by role >= company
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
        "scribe",                  # available; gated by role >= company
        "docs",                    # available; gated by role >= company
    }),
}

# Maps each capability string back to the seat-feature it requires.
# Used by `effective_capabilities()` to filter role-granted capabilities
# through what the seat actually unlocks. EXPLICIT mapping — no implicit
# string-prefix-matching, no convention. If a new capability lands without
# an entry here, the AC-13 lint catches it (every Capability member must
# appear as a key).
CAPABILITY_TO_SEAT_FEATURE: dict[str, str] = {
    # Connector capabilities
    "kb.connectors":           "kb.connectors",
    "kb.connectors.external":  "kb.connectors.external",
    # KB management capabilities — all unlocked by knowledge.full
    "kb.create_org":           "knowledge.full",
    "kb.members":              "knowledge.full",
    "kb.taxonomy":             "knowledge.full",
    "kb.gaps":                 "knowledge.full",
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
def effective_features(seat_type: SeatType, role: str) -> frozenset[str]:
    """What product surfaces does this user see?

    Seat unlocks the surface (chat / knowledge / scribe / docs).
    Role gates whether the user can actually open it. Personal-role
    users on a chat seat have scribe in SEAT_FEATURES but FEATURE_MIN_PROFILE
    keeps them out — they don't see scribe in the sidebar.

    v0.4.0: `.get(role, -1)` defends against unknown roles in the rank
    lookup — a future ladder-rename or a stale-snapshot history row
    must not be able to 500 this function. Unknown role → rank -1 →
    no features unlocked (fail-closed).
    """
    seat_unlocked = SEAT_FEATURES[seat_type]
    caller_rank = PROFILE_RANK.get(role, -1)
    return frozenset(
        f for f in seat_unlocked
        if caller_rank >= PROFILE_RANK.get(FEATURE_MIN_PROFILE.get(f, "personal"), -1)
    )

def effective_capabilities(role: str, seat_type: SeatType) -> frozenset[Capability]:
    """What permissions does the user have within unlocked features?

    Concrete algorithm (no hand-wave):
      1. Start with role-granted capabilities (PROFILE_CAPABILITIES[role]).
      2. For each capability, look up its required seat-feature in
         CAPABILITY_TO_SEAT_FEATURE (explicit mapping table above).
      3. Drop the capability if the seat does not unlock that feature.

    Examples:
      - kb_manager + knowledge seat
          role_caps = {kb.connectors, kb.connectors.external, kb.create_org,
                       kb.members, kb.taxonomy, kb.gaps}
          all required features unlocked by knowledge seat → all kept.
      - kb_manager + chat seat
          role_caps = same as above
          knowledge.full NOT in chat-seat features → drop kb.create_org,
          kb.members, kb.taxonomy, kb.gaps. Keep kb.connectors only.
          (kb.connectors.external dropped: chat seat lacks that feature.)
      - admin + viewer seat
          role_caps = full set
          viewer seat unlocks only chat_readonly + knowledge_readonly.
          NO mapped capability is satisfied → return frozenset().
          Admin still has admin powers via the role check (`require_at_least`),
          which is independent of capability gating.
    """
    role_caps = PROFILE_CAPABILITIES.get(role, frozenset())
    seat_unlocked = SEAT_FEATURES[seat_type]
    return frozenset(
        c for c in role_caps
        if CAPABILITY_TO_SEAT_FEATURE.get(c) in seat_unlocked
    )

def monthly_bill(org_id: int) -> int:
    """Per-seat-type headcount × per-seat price, summed across active users."""
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
| Capability resolution | `PROFILE_CAPABILITIES[role] ∩ PLAN_LIMITS[org.plan].capabilities` | `PROFILE_CAPABILITIES[role]` filtered through `CAPABILITY_TO_SEAT_FEATURE` against `SEAT_FEATURES[user.seat_type]` |
| `enabled_addons` (scribe/docs) | Workspace toggle, separately billed | **Already removed** by sibling SPEC-PORTAL-EXTENSIONS-UNIFY-001 (alembic head `e0ad7c2b1e80`, 2026-05-12). Both seats include scribe + docs; `FEATURE_MIN_PROFILE` keeps personal-role users out. v0.4.0: Phase 1 migration omits the redundant DROP. |
| `portal_user_seat_history` | Does not exist | **New table.** Append-only audit of seat changes (`user_id, seat_type, role, status, valid_from, valid_to`). v0.4.0: `status` snapshot + partial-unique `(user_id) WHERE valid_to IS NULL` + Postgres trigger + RLS (Cat-D). Required for prorated Phase 5 billing. |
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

### Scribe + docs are seat-included (no workspace toggle)

Mark v0.3.0 decision: scribe and docs are bundled into both paid seats (chat + knowledge). The `FEATURE_MIN_PROFILE` floor (`scribe → company`, `docs → company`) keeps personal-role users out. So the effective access matrix becomes:

| Seat × Role | Sees scribe + docs? |
|---|---|
| Chat seat + personal | NO (role floor) |
| Chat seat + company / kb_manager / group_manager / admin | YES |
| Knowledge seat + personal | NO (role floor) |
| Knowledge seat + company+ | YES |
| Viewer seat + any | NO (seat doesn't unlock) |

The `portal_orgs.enabled_addons` column is dropped in Phase 1 migration. No org-wide toggle anymore — if a tenant doesn't want scribe for *anyone*, they assign personal-role to all users (which already keeps them out today). Per-user opt-out of scribe specifically is out of scope; defer to a later SPEC if a customer asks.

### Seat-history table (portal_user_seat_history)

Today `portal_users` only knows current `status` and `created_at`. For Phase 5 prorated billing — "Roman was on knowledge seat for 12 days, then chat seat for 18 days, this month" — we need an append-only history.

```sql
CREATE TABLE portal_user_seat_history (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES portal_users(id) ON DELETE CASCADE,
    org_id          INT NOT NULL REFERENCES portal_orgs(id),
    seat_type       TEXT NOT NULL CHECK (seat_type IN ('viewer', 'chat', 'knowledge')),
    role            TEXT NOT NULL,        -- snapshot of role at the time
    status          TEXT NOT NULL,        -- v0.4.0: snapshot of portal_users.status
                                          -- ('active'|'suspended'|'offboarded' per
                                          --  portal_users.ck_portal_users_status)
                                          -- so Phase 5 prorate can scope to billable
                                          -- windows (only 'active' is billable).
    valid_from      TIMESTAMPTZ NOT NULL,
    valid_to        TIMESTAMPTZ,          -- NULL = current row, set on next change
    changed_by      VARCHAR(64),          -- zitadel_user_id of admin who made the change
    change_reason   TEXT,                 -- 'invite', 'role_change', 'seat_change',
                                          -- 'status_change', 'backfill'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pu_seat_hist_user_validto ON portal_user_seat_history(user_id, valid_to);
CREATE INDEX idx_pu_seat_hist_org_validfrom ON portal_user_seat_history(org_id, valid_from);

-- v0.4.0: partial-unique guarantees at most ONE open (current) row per user.
-- Combined with the Postgres trigger below, this serializes concurrent
-- UPDATEs on the same portal_users row — no overlapping "current" history.
CREATE UNIQUE INDEX idx_pu_seat_hist_one_open_per_user
    ON portal_user_seat_history (user_id)
    WHERE valid_to IS NULL;
```

**v0.4.0: Postgres trigger replaces SQLAlchemy event listener.** ORM events miss `session.execute(update(...).values(...))`-style bulk updates (which several admin scripts use), so an audit gap can land silently. A `BEFORE UPDATE OR INSERT` trigger on `portal_users` is immune to ORM-bypass and runs in the same transaction:

```sql
CREATE OR REPLACE FUNCTION portal_users_seat_history_trg() RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(), 'invite');
        RETURN NEW;
    END IF;
    -- UPDATE path: only fire when an audited column changed
    IF (NEW.seat_type IS DISTINCT FROM OLD.seat_type)
       OR (NEW.role     IS DISTINCT FROM OLD.role)
       OR (NEW.status   IS DISTINCT FROM OLD.status) THEN
        -- Close the previous "current" row
        UPDATE portal_user_seat_history
           SET valid_to = NOW()
         WHERE user_id = NEW.id
           AND valid_to IS NULL;
        -- Append the new current row
        INSERT INTO portal_user_seat_history
            (user_id, org_id, seat_type, role, status, valid_from, change_reason)
        VALUES
            (NEW.id, NEW.org_id, NEW.seat_type, NEW.role::text, NEW.status::text,
             NOW(),
             CASE
                 WHEN NEW.seat_type IS DISTINCT FROM OLD.seat_type THEN 'seat_change'
                 WHEN NEW.role      IS DISTINCT FROM OLD.role      THEN 'role_change'
                 ELSE 'status_change'
             END);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER portal_users_seat_history
    AFTER INSERT OR UPDATE ON portal_users
    FOR EACH ROW EXECUTE FUNCTION portal_users_seat_history_trg();
```

**v0.4.0: RLS on the history table (Cat-D pattern).** Phase 5 queries this table per-org for prorate billing; without RLS a buggy aggregation could leak rows across tenants. Schema-qualified helper avoids the return-type-overload trap (see `postgres-no-return-type-overload` pitfall):

```sql
-- Schema-qualified helper. Distinct from portal-api's existing
-- public._rls_current_org_id() (which returns integer) to avoid
-- the return-type-overload collision documented in process-rules.md.
CREATE SCHEMA IF NOT EXISTS billing;

CREATE OR REPLACE FUNCTION billing._rls_current_org_id() RETURNS integer AS $$
    SELECT NULLIF(current_setting('app.current_org_id', true), '')::integer;
$$ LANGUAGE sql STABLE;

ALTER TABLE portal_user_seat_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_user_seat_history FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON portal_user_seat_history
    USING      (org_id = billing._rls_current_org_id())
    WITH CHECK (org_id = billing._rls_current_org_id());
```

This RLS DDL runs in the post-deploy SQL block (klai-owned, alembic role cannot ENABLE RLS on its own — see `alembic-cannot-drop-non-portal_api-tables` pitfall, which extends to ENABLE RLS / CREATE POLICY). Phase 1 ships an `alembic/versions/post_deploy_<rev>.sql` file alongside the migration; the operator applies it as `klai` superuser after `alembic upgrade head` succeeds.

Phase 1 migration:
- Create the table (alembic, runs as `portal_api`)
- Backfill: for each existing `portal_users` row, INSERT one history row with `valid_from = portal_users.created_at`, `valid_to = NULL`, `change_reason = 'backfill'`, `status` copied from `portal_users.status`
- Install the Postgres trigger (alembic — function + trigger are portal_api-ownable)
- Post-deploy SQL: ENABLE / FORCE RLS + CREATE POLICY (klai-superuser path; `apply_post_deploy_sql.sh`)

Phase 5 prorate query (illustrative, v0.4.0 — scopes by `status` so deactivate-windows don't bill):
```sql
SELECT
  user_id,
  seat_type,
  EXTRACT(EPOCH FROM (LEAST(COALESCE(valid_to, $period_end), $period_end)
                      - GREATEST(valid_from, $period_start))) / 86400 AS days_active
FROM portal_user_seat_history
WHERE org_id = $org_id
  AND status = 'active'                            -- v0.4.0: only billable rows
  AND valid_from < $period_end
  AND (valid_to IS NULL OR valid_to > $period_start)
;
```

Sum `days_active × (SEAT_PRICE / days_in_period)` per seat_type for the bill.

---

## Migration plan

Six phases. Each is independently shippable; rollback at any phase leaves prod functional.

### Phase 1 — Add seat_type + seat_history + read-only billing breakdown (~1.5 days)

- Add `seats.py` module with `SeatType`, `SEAT_FEATURES`, `CAPABILITY_TO_SEAT_FEATURE`, prices, helpers (v0.4.0: `effective_features` uses `.get(role, -1)` for unknown-role safety)
- Alembic migration (chains off `e0ad7c2b1e80`, the EXTENSIONS-UNIFY head verified 2026-05-12):
  ```sql
  -- 1. Add seat_type column + backfill from current role
  ALTER TABLE portal_users ADD COLUMN seat_type TEXT;
  UPDATE portal_users SET seat_type = CASE
      WHEN role IN ('personal', 'company') THEN 'chat'
      WHEN role IN ('kb_manager', 'group_manager', 'admin') THEN 'knowledge'
      ELSE 'chat'
  END;
  ALTER TABLE portal_users ALTER COLUMN seat_type SET NOT NULL;
  ALTER TABLE portal_users ADD CONSTRAINT portal_users_seat_type_check
      CHECK (seat_type IN ('viewer', 'chat', 'knowledge'));

  -- 2. (REMOVED v0.4.0) "DROP COLUMN enabled_addons" — column was already
  --    dropped by SPEC-PORTAL-EXTENSIONS-UNIFY-001 (alembic head
  --    e0ad7c2b1e80, merged 2026-05-12). Running it here crashes.

  -- 3. Create seat-history table (Phase 5 dependency, but row is cheap; create early)
  CREATE TABLE portal_user_seat_history (
      id              BIGSERIAL PRIMARY KEY,
      user_id         BIGINT NOT NULL REFERENCES portal_users(id) ON DELETE CASCADE,
      org_id          INT NOT NULL REFERENCES portal_orgs(id),
      seat_type       TEXT NOT NULL CHECK (seat_type IN ('viewer', 'chat', 'knowledge')),
      role            TEXT NOT NULL,
      status          TEXT NOT NULL,   -- v0.4.0: billable-window snapshot
      valid_from      TIMESTAMPTZ NOT NULL,
      valid_to        TIMESTAMPTZ,
      changed_by      VARCHAR(64),
      change_reason   TEXT,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  CREATE INDEX idx_pu_seat_hist_user_validto ON portal_user_seat_history(user_id, valid_to);
  CREATE INDEX idx_pu_seat_hist_org_validfrom ON portal_user_seat_history(org_id, valid_from);
  -- v0.4.0: partial-unique enforces "at most one open row per user".
  CREATE UNIQUE INDEX idx_pu_seat_hist_one_open_per_user
      ON portal_user_seat_history (user_id) WHERE valid_to IS NULL;

  -- 4. Backfill history: one row per existing user (carries status snapshot)
  INSERT INTO portal_user_seat_history
      (user_id, org_id, seat_type, role, status, valid_from, change_reason)
  SELECT id, org_id, seat_type, role::text, status::text, created_at, 'backfill'
    FROM portal_users;

  -- 5. v0.4.0: Install BEFORE/AFTER UPDATE trigger (replaces ORM event-listener)
  --    Body defined in the "Seat-history table" section above.
  CREATE OR REPLACE FUNCTION portal_users_seat_history_trg() RETURNS TRIGGER AS $$ ... $$;
  CREATE TRIGGER portal_users_seat_history
      AFTER INSERT OR UPDATE ON portal_users
      FOR EACH ROW EXECUTE FUNCTION portal_users_seat_history_trg();
  ```
  Existing `kb_manager` users keep their KM features (Knowledge seat), `personal` users keep theirs (Chat seat). Zero behavior change at this phase.

- **Post-deploy SQL** (`alembic/versions/post_deploy_<rev>.sql`, applied as `klai` superuser via `apply_post_deploy_sql.sh`):
  ```sql
  -- RLS for the new history table (Cat-D pattern, schema-qualified helper).
  CREATE SCHEMA IF NOT EXISTS billing;
  CREATE OR REPLACE FUNCTION billing._rls_current_org_id() RETURNS integer AS $$
      SELECT NULLIF(current_setting('app.current_org_id', true), '')::integer;
  $$ LANGUAGE sql STABLE;

  ALTER TABLE portal_user_seat_history ENABLE ROW LEVEL SECURITY;
  ALTER TABLE portal_user_seat_history FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON portal_user_seat_history
      USING      (org_id = billing._rls_current_org_id())
      WITH CHECK (org_id = billing._rls_current_org_id());
  ```

- v0.4.0: NO SQLAlchemy event listener (the Postgres trigger replaces it). This sidesteps `session.execute(update(...))` ORM-bypass and concurrent-update races.
- v0.4.0: NO `/admin/settings/addons` UI to drop — already removed by SPEC-PORTAL-EXTENSIONS-UNIFY-001 (#594, Phase 4). Verified zero `enabled_addons` consumer code outside the BC wire-name (which stays).
- Add `GET /api/admin/billing/breakdown` endpoint returning `{seat_type: count}` — **v0.4.0: requires `require_at_least("admin")` AND tenant scope** (FastAPI dependency, mirrors the existing `/admin/*` router gate). Tests: non-admin → 403; cross-tenant → only own org counts.
- Add display-only "Per-seat breakdown" panel on `/admin/billing`
- Tests: seat_type defaults derived correctly, breakdown endpoint counts + RBAC + tenant-scope, trigger appends history row on every audited UPDATE (and on bulk `session.execute(update(...))` — explicit test for ORM-bypass safety), partial-unique blocks a manual double-open insert

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

### Phase 5 — Per-seat-type Moneybird billing with prorated daily-rate (~2 days)

- `MoneybirdService.update_subscription(org)` switches to per-seat-type line-items
- Bill-amount source-of-truth shifts from `org.plan × org.seats` to a query on `portal_user_seat_history` covering the billing period:
  ```python
  for each seat_type in (chat, knowledge, viewer):
      seat_days = sum(days_active per user in this seat_type within period)
      seat_billable = seat_days × (SEAT_PRICE_MONTHLY[seat_type] / days_in_period)
  ```
- Cost-delta forecast for the admin-confirm CTA shows BOTH the snapshot bill (today's headcount × monthly price) AND the historical-actual bill (current month's daily prorated). Admin sees both numbers before clicking.
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

**AC-7**: WHEN admin views `/admin/billing`, the page SHALL display per-seat-type user counts AND per-seat-type monthly cost AND the org's total monthly cost. The backing endpoint `GET /api/admin/billing/breakdown` (v0.4.0): SHALL require `require_at_least("admin")` AND SHALL scope counts to the caller's tenant only. Tests SHALL verify non-admin → 403 AND cross-tenant org_id in URL → 404/403 (no count-leak across orgs).

**AC-8**: WHEN admin views `/admin/users`, each row SHALL display two columns: "Role" and "Seat" (each with the current value, both editable).

**AC-9**: WHEN `MoneybirdService.update_subscription(org)` runs (Phase 5+), it SHALL produce one line-item per active seat type with the correct headcount and per-seat price.

**AC-10**: WHEN a `viewer` seat is assigned, the user SHALL have read-only access to chat + knowledge surfaces AND incur €0 billing impact AND NOT count against any per-seat license cap (because there is none after Phase 3).

**AC-11**: WHEN `assert_role_allowed_for_plan` is called from any production code path after Phase 3, the system SHALL emit a `DeprecationWarning` AND return without raising — no-op for one release cycle, then removed.

**AC-12**: WHEN any tenant's billing is migrated to per-seat (Phase 5), the migration SHALL only happen after admin click on `/admin/billing` "switch to per-user billing" CTA. Until clicked, the tenant remains on legacy `plan × seats` billing. NO automatic per-tenant billing change.

**AC-13** (regression guard): WHEN any future code path tries to derive the seat type from the role automatically (the v0.1.0 anti-pattern), CI SHALL fail. An ast-grep rule MUST live at `rules/no-profile-derives-seat.yml` AND a matching test-fixture set MUST live at `rules/tests/no-profile-derives-seat-tests.yml`. The rule is exercised by `sg test -t rules/tests/` in the portal-api workflow on every PR.

**v0.4.0 — rule-body and fixture contract.** The implementation PR MUST satisfy the following observable behavior. The exact ast-grep pattern is left to the implementer because ast-grep's dict-key-across-`$$$` matching is order-dependent and brittle — v0.3.0 baked patterns that did not actually fire. Acceptable strategies: (a) `pattern: $X = {$$$}` constrained by a `has` clause requiring both a role-string literal and a `SeatType.$ANY` inside, (b) `pattern: SeatType.$ANY` constrained by `inside` a dict-literal context with an adjacent role-string sibling, or (c) a small wrapper script doing AST-search via `sg run` and post-filtering — whichever yields the fixture matrix below.

```yaml
# rules/no-profile-derives-seat.yml — frontmatter contract (rule body filled by implementer)
id: no-profile-derives-seat
language: python
severity: error
message: |
  Direct seat-from-role mapping outside suggest_seat() conflates the billing
  axis with the permission axis (SPEC-PORTAL-PRICING-PER-USER-001 anti-pattern).
  Use suggest_seat(role) for smart-defaults; admin overrides go through the
  explicit seat selector. See spec section "Why decoupled".
fix-suggestion: |
  Use the explicit suggest_seat() helper:
      seat = suggest_seat(role)  # returns the recommended default
      # admin override path: seat is set independently from role
files:
  include:
    - "klai-portal/backend/app/**/*.py"
  exclude:
    - "klai-portal/backend/app/core/seats.py"     # canonical mapping site
    - "klai-portal/backend/tests/**"               # fixtures may inline
    - "klai-portal/backend/alembic/versions/**"   # v0.4.0: data-migration may inline
```

The rule's required fixture matrix (all must be observed-correct under `sg test`):

| # | Fixture | Expected verdict |
|---|---|---|
| P1 | `MY_MAP = {"personal": SeatType.CHAT, "kb_manager": SeatType.KNOWLEDGE}` outside `seats.py` | **FAIL** |
| P2 | `seat_for_role = {"company": SeatType.CHAT}` (subset of role keys) | **FAIL** |
| P3 | `def my_seat(role): return SeatType.KNOWLEDGE if role == "kb_manager" else SeatType.CHAT` | **FAIL** |
| P4 | `MY_MAP[role] = SeatType.CHAT` (subscript-assignment in non-canonical file) | **FAIL** |
| P5 | Same as P1 but in a fenced docstring/comment block | **FAIL** (lint sees source text; manual `# noqa` allowed if intentional doc-example) |
| N1 | `seat = suggest_seat(role)` | PASS |
| N2 | `seat = SeatType.CHAT  # literal, no role coupling` | PASS |
| N3 | Same as P1 but located inside `app/core/seats.py` | PASS (canonical site, excluded by `files.exclude`) |

The exception `app/core/seats.py` is the SINGLE place the mapping is allowed (definition site). Tests are excluded so they can build fixtures explicitly. `alembic/versions/**` is excluded (v0.4.0) because data-migrations may inline a one-shot backfill mapping for legacy rows — the Phase 1 migration itself uses raw SQL CASE, not Python dict, but future migrations may not. The rule is fail-loud — a positive fixture that the rule does NOT catch breaks `sg test` in CI.

---

## Pre-flight check (MUST run before Phase 1 ships)

```sql
SELECT o.slug, COUNT(u.zitadel_user_id) AS km_count
FROM portal_orgs o
JOIN portal_users u ON u.org_id = o.id
WHERE o.plan = 'chat' AND u.role IN ('kb_manager', 'group_manager')
GROUP BY o.slug;
```

After SPEC-PORTAL-PLAN-RENAME-001 deploy, allowlist enforcement should mean ZERO rows. If non-zero, those users default to `knowledge` seat in the Phase 1 backfill (matches their role's expected access), and the affected admin gets a one-time email noting the seat assignment and any billing impact (Phase 5 onwards).

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Phase 5 Moneybird migration accidentally double-bills tenants | Per-tenant feature flag `BILLING_PER_SEAT_ENABLED`, OFF by default. Admin click required. |
| Removing `ALLOWED_PROFILES_PER_PLAN` triggers test failures across 30+ test files | Phase 3 single-PR sweep with one bulk-update commit (mechanical, like SPEC-PORTAL-PLAN-RENAME-001). Tests asserting `role_not_allowed_for_plan` are inverted to assert success. |
| `portal_orgs.plan` is referenced by external callers / SOPS env / dashboards | Phase 6 leaves the column NULL-able and untouched for one release. Observability dashboards updated separately. |
| Mixed-seat billing line-items break Moneybird's existing template | Test against Moneybird sandbox before Phase 5 ships per tenant. Rollback plan: revert to legacy single-tier billing per affected tenant. |
| Admin demotes user OR lowers seat → silent capability loss the user never agreed to | Phase 2 confirm-modal includes consequence ("Roman will lose access to org KBs and KB management features") and emits a `user.seat_changed` audit event. |
| `viewer` seat triggers free-tier abuse (org creates 1000 viewer seats) | Defer abuse-rate-limiting to a later SPEC if observed in practice. v0.2.0 trusts admins. |
| Profile-derives-seat anti-pattern creeps back via "convenient" code | AC-13 ast-grep CI rule blocks direct `SEAT_FOR_ROLE[role]` outside the explicit `suggest_seat()` helper. v0.4.0 strengthened with fixture-matrix testing under `sg test`. |
| History-row append misses bulk `session.execute(update(...).values(...))` writes (ORM event-listener bypass) | **v0.4.0:** Postgres `AFTER INSERT OR UPDATE` trigger replaces the ORM listener — fires regardless of how the UPDATE arrived. Plus partial-unique `(user_id) WHERE valid_to IS NULL` serializes concurrent writes. |
| Cross-tenant data-leak via aggregate query on `portal_user_seat_history` | **v0.4.0:** RLS enabled at create-table time (Cat-D pattern, `billing._rls_current_org_id()` schema-qualified helper). FORCE RLS prevents accidental bypass even by table owner. |
| Phase 1 alembic migration crashes because `portal_orgs.enabled_addons` column does not exist | **v0.4.0:** DROP-COLUMN step removed — column already dropped by SPEC-PORTAL-EXTENSIONS-UNIFY-001 at head `e0ad7c2b1e80` (verified on prod 2026-05-12). |
| `effective_features` 500s for an unknown role (e.g. legacy snapshot value in `portal_user_seat_history`) | **v0.4.0:** `PROFILE_RANK.get(role, -1)` returns rank -1 → no features unlocked → fail-closed instead of crash. |

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

Status: **ready-for-run** — sparring resolved (v0.3.0). Run `/moai run SPEC-PORTAL-PRICING-PER-USER-001` to start Phase 1.
