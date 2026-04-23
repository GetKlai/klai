---
id: SPEC-BILLING-UPGRADE-001
version: "0.1.0"
status: draft
created: 2026-04-23
author: Mark Vletter
priority: medium
---

# SPEC-BILLING-UPGRADE-001: Self-serve upgrade flow

## Summary

Vervolg op SPEC-PORTAL-UNIFY-KB-001. In die SPEC zijn capability-gated tabs en
quota-knoppen grijs gemaakt zonder click-gedrag ("disabled, not hidden"). Deze
SPEC voegt de klikbare upgrade-CTA toe: grijs element → click → checkout → betaling.

Scope: een klik op een grayed-out tab of knop (met `data-capability-guard` of
`data-quota-guard` attribuut) opent de self-serve checkout flow voor het
Klai Knowledge plan.
