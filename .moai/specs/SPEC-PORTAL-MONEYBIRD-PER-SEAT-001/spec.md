---
id: SPEC-PORTAL-MONEYBIRD-PER-SEAT-001
version: "0.2.0"
status: needs-research
created: 2026-05-13
updated: 2026-05-13
author: Mark Vletter
priority: medium
supersedes:
  - SPEC-PORTAL-PRICING-PER-USER-001 Phase 5 (only the Moneybird mutation portion — Phase 5 light + Phases 1-6 already shipped via PRs #599 / #608 / #609 / #611 / #612 / #614 / #616)
related:
  - SPEC-PORTAL-PRICING-PER-USER-001 (parent SPEC — seat_type axis, breakdown endpoint, feature-flag column)
  - SPEC-BILLING-UPGRADE-001 (Moneybird subscription wiring — TOUCHED)
---

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-13 | 0.1.0 | Initial draft. Three-tier per-seat Moneybird design (chat / knowledge / viewer). Investigation Q1–Q5 listed. Status: `needs-research`. |
| 2026-05-13 | 0.2.0 | **Viewer tier dropped.** Parent SPEC v0.5.0 narrowed `SeatType` to `{CHAT, KNOWLEDGE}` after Mark confirmed [getklai.com/pricing](https://getklai.com/pricing) has only two tiers — viewer never existed on the marketing page. All references to the third `viewer × K` subscription removed: Option A becomes "at most two subscriptions per contact", Design A loses `moneybird_subscription_viewer_id`. The pre-flight `seat_type` distribution query now expects values from `{chat, knowledge}` only. Status stays `needs-research` (Q1–Q5 still open). |

# SPEC-PORTAL-MONEYBIRD-PER-SEAT-001: Moneybird per-seat-type subscription rewrite

## Summary

Klai's parent SPEC ([SPEC-PORTAL-PRICING-PER-USER-001](../SPEC-PORTAL-PRICING-PER-USER-001/spec.md)) shipped six phases that decouple billing (`seat_type`) from permissions (`role`). The frontend at `/admin/billing` shows the per-seat breakdown ("you would pay €X under per-user pricing"), but Moneybird still invoices the **legacy** shape: one `org.plan × org.seats` subscription per tenant. This SPEC closes that last gap.

Concretely: today voys.getklai.com has 7 knowledge-seat users and Moneybird bills them on a single subscription of `plan=knowledge × seats=7 = €476/mo`. The right shape post-this-SPEC: one subscription with per-seat-type line-items so a future mixed-seat org (e.g. 4 chat + 1 knowledge) gets the math right (`4 × €28 + 1 × €68 = €180/mo` instead of `5 × €68 = €340/mo` or `5 × €28 = €140/mo`).

The trigger for the per-tenant switch-over is admin-driven (`POST /api/admin/billing/switch-to-per-seat` — Phase 5 light returns 501; this SPEC implements the real body). No automatic migration. Per-tenant rollback playbook included.

---

## Problem statement

Three concrete gaps the parent SPEC left:

1. **MoneybirdService doesn't support per-seat-type line-items.** Today `create_subscription(contact_id, product_id, frequency_type, quantity)` takes a single product + quantity. Per-seat-type means either:
   - **Option A**: Up to two subscriptions per contact (`chat × N + knowledge × M`). Moneybird supports multiple active subscriptions per contact. (Parent SPEC v0.5.0 dropped the viewer tier — only two billable tiers exist.)
   - **Option B**: One subscription with multiple line-items via Moneybird's product-bundle mechanism (needs API research — see Investigation Needed below).
   - **Option C**: Custom invoice template (sends invoices outside the subscription system).

2. **No migration code from legacy → per-seat.** When admin clicks "switch to per-user billing", we need to:
   - Cancel the existing single-tier subscription on the next billing-cycle boundary.
   - Create the per-seat-type subscription(s) starting the day after cancellation.
   - Persist `portal_orgs.billing_per_seat_enabled = TRUE`.
   - Send the lifecycle email confirming the switch (with before/after cost).

3. **No automatic seat-count update.** Phase 5b must also wire the **ongoing** Moneybird sync: when an admin invites a 6th user (Phase 2 endpoint), the per-seat subscription's quantity for that tier must bump. Today's `app/api/webhooks.py` updates the single subscription's quantity to `org.seats`; the new path needs to compute the per-tier breakdown and update each tier's subscription separately.

---

## Investigation needed (BLOCKS implementation)

The parent SPEC v0.3.0 deferred the Moneybird-side research. This SPEC needs to answer:

**Q1**: Does Moneybird's subscription API support per-line-item adjustment, or is each subscription strictly one product? Read [Moneybird Subscription API docs](https://developer.moneybird.com/api/subscriptions/) end-to-end. Confirm or reject Option B.

**Q2**: What's the smallest unit of "subscription change" Moneybird supports? Can we update `quantity` mid-cycle, or only at renewal? Pro-ration: does Moneybird handle it automatically, or does Klai compute the per-seat-day total?

**Q3**: How does Moneybird treat **subscription cancellation + immediate new subscription** for the same contact? Is there a customer-visible "gap day" or invoice on the cancellation? Cancel-and-replace might trigger a refund + new invoice the customer sees as "we got billed twice".

**Q4**: Does Moneybird have a sandbox / test admin we can use? If not, the first Phase 5b deploy goes against a Klai-internal test tenant (e.g. provision a one-off `klai-billing-test` org with a real but Klai-owned Moneybird contact). This is the canonical pre-launch pattern from SPEC-BILLING-UPGRADE-001.

**Q5**: What's the SEPA mandate behaviour on subscription replacement? Does the existing mandate carry to the new subscription(s), or does the admin need to re-authorise? Re-authorisation = customer pain point.

Until Q1–Q5 have answers, status stays `needs-research`. Sparring with [`gtm-pricing-strategy`](../../.claude/skills/gtm-pricing-strategy/SKILL.md) and a Moneybird-support-ticket round is the next step before this becomes `ready-for-run`.

---

## Architecture (provisional, depends on investigation answers)

Two candidate designs. The investigation determines which is feasible:

### Design A — One subscription per seat tier (the simple path)

```
portal_orgs.moneybird_contact_id stays as today.
portal_orgs.moneybird_subscription_id is renamed to moneybird_subscription_chat_id.
NEW: portal_orgs.moneybird_subscription_knowledge_id.

When admin clicks switch:
1. Cancel the legacy subscription.
2. Create up to two new subscriptions (omit any tier with zero active users):
   - chat-tier subscription with quantity = COUNT(seat_type='chat')
   - knowledge-tier subscription with quantity = COUNT(seat_type='knowledge')
3. Set billing_per_seat_enabled = TRUE.

When seat assignment changes:
- update_user_seat / invite_user computes the new per-tier counts.
- For each tier whose count changed, update its subscription's quantity via Moneybird.
- Moneybird auto-pro-rates the difference.
```

**Pro**: Trivial mapping; each line on the Moneybird invoice corresponds to a tier.
**Con**: Up to two subscriptions per tenant. Cancelling on offboard means cancelling each separately.

### Design B — One subscription with per-tier line-items (preferred if Moneybird API supports it)

Single subscription per tenant, but with the line-item structure expressing the breakdown. Requires Moneybird API support that we haven't yet verified.

**Pro**: One subscription per tenant matches the customer's mental model. One invoice line per tier.
**Con**: API risk — if Moneybird doesn't support this, falls back to Design A.

### Design choice will be made after Q1.

---

## Acceptance criteria (EARS)

Provisional — refined after investigation. Mark fields are placeholders.

**AC-1**: WHEN admin clicks "Switch to per-user billing" on `/admin/billing` AND the org is currently on `billing_status='active'`, the system SHALL:
- Display a confirm modal with the before/after monthly cost.
- On confirm, call the chosen design's Moneybird mutation path (cancel + create / replace).
- Set `portal_orgs.billing_per_seat_enabled = TRUE`.
- Emit a `billing.switched_to_per_seat` audit event with old + new monthly total.
- Send a lifecycle email to the billing contact.

**AC-2**: WHEN a tenant has `billing_per_seat_enabled = TRUE` AND a user's seat_type changes (via Phase 2's PATCH endpoint OR invite/offboard), the system SHALL recompute the per-tier breakdown and update Moneybird via the chosen design's path within 5 seconds.

**AC-3**: WHEN any Moneybird mutation in Phase 5b fails (API timeout, 4xx, 5xx), the system SHALL:
- Roll back any partial state (re-create the cancelled legacy subscription IF the new ones were not created).
- Emit a `billing.per_seat_mutation_failed` audit event with the failure reason.
- Surface a 503 to the admin with a "retry or contact support" message.
- NOT change `billing_per_seat_enabled` from FALSE → TRUE on partial failure.

**AC-4**: WHEN the per-seat migration runs on a Klai-internal test tenant for the first time, an operator SHALL verify the resulting Moneybird invoice line-items match the expected per-tier breakdown to the cent before any real customer tenant is offered the switch.

**AC-5**: WHEN a customer admin pays a per-seat invoice and the mandate is the same one from the legacy subscription, the SEPA debit SHALL succeed without re-authorisation. (Depends on Q5 outcome.)

---

## Phases

This SPEC has its own phases nested INSIDE Phase 5b of the parent:

### Phase 5b.1 — Investigation (~2-3 days)

- Read Moneybird subscription + line-item + invoice API docs end-to-end.
- Open a Moneybird-support ticket for Q1, Q2, Q5.
- Provision a test tenant (`klai-billing-test`) with a real Moneybird contact owned by Klai.
- Document findings in `.moai/specs/SPEC-PORTAL-MONEYBIRD-PER-SEAT-001/research.md`.
- Promote SPEC status to `ready-for-run` only after research.md exists with concrete answers.

### Phase 5b.2 — `MoneybirdService` rewrite (~1 day, depends on 5b.1)

- Add chosen design's methods (e.g. `update_subscriptions_for_org(org, breakdown)`).
- Keep `create_subscription` + `cancel_subscription` for the legacy path until 5b.5 (column rename window).
- Unit tests against mocked Moneybird HTTP responses for happy path + each failure mode.
- Integration test against the Klai test tenant.

### Phase 5b.3 — Switch endpoint + admin UI (~1 day)

- Replace the Phase 5-light 501 stub at `POST /api/admin/billing/switch-to-per-seat` with the real flow.
- FE: enable the CTA when `per_seat_status.available = true` (driven by a settings flag or DB attr, not hard-coded).
- Confirm-modal with before/after totals.
- Audit event emission.

### Phase 5b.4 — Ongoing sync hook (~½ day)

- Hook into `update_user_seat` + `invite_user` + offboard paths to fire `update_subscriptions_for_org` post-commit.
- Test: invite 6th user when tenant is on per-seat-billing → Moneybird subscription quantity bumps within 5s.

### Phase 5b.5 — Phase-out the legacy column / endpoint (~½ day, deferred)

- After 100% of active tenants are on `billing_per_seat_enabled=TRUE`, schedule the column-drop migration for the next release.
- Until then, the legacy path keeps working for non-migrated tenants.

### Phase 5b.6 — Update parent SPEC HISTORY (~5 min)

- Bump SPEC-PORTAL-PRICING-PER-USER-001 to v0.6.0 noting Phase 5 was split: Phase 5 (light) shipped via #614 + #616, Phase 5b shipped via THIS SPEC. Status: `shipped`.

---

## Out of scope

- **Multi-currency**: EUR throughout (matches parent SPEC's out-of-scope clause).
- **Annual-contract math beyond Moneybird's built-in pro-ration**: rely on Moneybird's standard behaviour.
- **Per-add-on per-user pricing** (scribe-per-user etc.): parent SPEC already deferred this.
- **Self-service downgrade from per-seat back to legacy**: not supported. Once a tenant opts in, they stay opted in. (Reverting requires a Klai-side intervention + a SPEC; not exposed to customer admins.)

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Moneybird API doesn't support Design B → fallback to Design A (3 subscriptions per tenant) | Investigation answers this in Phase 5b.1 before any code lands. |
| Cancel-and-replace creates a "gap day" or double-invoice the customer sees | Q3 investigation. If real, Phase 5b.3 schedules the switch at the next billing-cycle boundary, not immediately. |
| SEPA mandate doesn't carry to new subscriptions → re-authorisation needed | Q5 investigation. If real, the FE switch-flow has a step that explains + initiates the new mandate. Worst case: defer Phase 5b until Moneybird supports mandate-portability OR the customer onboarding flow is reworked. |
| Moneybird sandbox doesn't exist → first test runs against real Klai-owned contact | Acceptable per AC-4. Klai-internal test tenant is the canonical pattern from SPEC-BILLING-UPGRADE-001. |
| Mutation race: two concurrent seat-changes both fire `update_subscriptions_for_org` with stale snapshots | Serialize the Moneybird mutation per org via an in-process asyncio.Lock keyed on `org.id`. Acceptable because portal-api is single-replica today; if it ever scales horizontally, swap to a Redis-backed lock. Audit log gets a `billing.mutation_serialized` event when contention is detected. |
| Operator forgets to flip `per_seat_status.available` from FALSE to TRUE after sandbox-OK | Wire `available` to a settings flag (`PER_SEAT_BILLING_ROLLOUT_AVAILABLE`) so the flip is a SOPS-env-update, not a code-deploy. (This addresses a real-known issue from the Phase 5 light shipped code — `available` is hard-coded today.) |

---

## Pre-flight checks before Phase 5b.3 deploys

```sql
-- Confirm no tenant is mid-mandate at switch time. Mid-mandate means the
-- legacy subscription is in 'mandate_requested' state and cancelling it
-- would leave the tenant with no billing.
SELECT id, slug, billing_status FROM portal_orgs WHERE billing_status NOT IN ('active', 'cancelled');
-- Expected: zero rows. If non-zero, the affected tenants need to complete
-- mandate-auth FIRST, before they can switch to per-seat.

-- Confirm `seat_type` distribution matches the expected post-Phase-1 backfill
-- (or current state if seat-changes have happened since).
SELECT o.slug, u.seat_type, COUNT(*)
FROM portal_orgs o JOIN portal_users u ON u.org_id = o.id
WHERE u.status = 'active'
GROUP BY o.slug, u.seat_type
ORDER BY o.slug, u.seat_type;
```

---

## Definition of done

- All AC-1 through AC-5 passing on the Klai test tenant + at least one real customer-tenant after consent.
- `MoneybirdService.update_subscriptions_for_org` (or its renamed equivalent) shipped.
- `POST /api/admin/billing/switch-to-per-seat` replaces the 501 stub with the real flow.
- FE `available` is settings-driven (the Phase 5 light hard-code is gone).
- Parent SPEC bumped to v0.6.0 with HISTORY row marking Phase 5b shipped.
- Pitfall captured: `moneybird-cancel-and-replace-mandate-pitfall` IF Q5 reveals one.
- No legacy `org.plan × org.seats` invoices for tenants who've opted in.

---

## Sources / prior art to read before research

- [Moneybird Subscription API](https://developer.moneybird.com/api/subscriptions/)
- [Moneybird Invoice line-items](https://developer.moneybird.com/api/sales_invoices/)
- [Moneybird Webhooks reference](https://developer.moneybird.com/webhooks/)
- Existing `klai-portal/backend/app/services/moneybird.py` — current single-product subscription path
- Existing `klai-portal/backend/app/api/webhooks.py` — Moneybird webhook handler that bumps `quantity = org.seats`
- Parent SPEC-PORTAL-PRICING-PER-USER-001 Section "Risks and mitigations" — pre-thought-through failure modes from v0.4.0 + v0.4.0 hotfix lessons
- `.claude/rules/klai/pitfalls/process-rules.md::rls-with-check-blocks-migration-update` — Phase 5b's eventual UPDATE on portal_orgs is safe (no RLS) but new tables introduced here MUST follow the RLS pattern

---

## What is NOT this SPEC's responsibility

- Anything already shipped by [SPEC-PORTAL-PRICING-PER-USER-001](../SPEC-PORTAL-PRICING-PER-USER-001/spec.md) Phases 1-4 and 5-light + 6. That work is `LIVE` on prod as of 2026-05-13 (PRs #599 / #608 / #609 / #611 / #612 / #614 / #616).
- Quota-axis swap (`effective_kb_limits(role, plan)` still uses plan). Defer to a separate SPEC once Phase 5b is shipped — once `plan` is truly dead the quota helper can swap too.

---

Status: **needs-research** — investigation Q1–Q5 before code lands. Recommended sparring partners: `gtm-pricing-strategy` skill for the customer-experience side, Moneybird support for the API side.
