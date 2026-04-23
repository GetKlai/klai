---
id: SPEC-PORTAL-GRANDFATHER-001
version: "0.1.0"
status: draft
created: 2026-04-23
author: Mark Vletter
priority: medium
---

# SPEC-PORTAL-GRANDFATHER-001: Per-org overrides op PLAN_LIMITS

## Summary

Sommige early-adopter organisaties hebben contractuele afspraken die afwijken van
de standaard plan-limieten. Deze SPEC implementeert per-org overrides via de stub
`get_effective_limits(org_id, db)` die al aanwezig is in
`app/core/plan_limits.py` (SPEC-PORTAL-UNIFY-KB-001 R-O1).

Scope: DB-tabel of config-tabel met per-org `KBLimits`-overrides. De functie
`get_effective_limits` leest de override indien aanwezig, anders `get_plan_limits(org.plan)`.
