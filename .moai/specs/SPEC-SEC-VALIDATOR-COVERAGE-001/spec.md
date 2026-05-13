---
id: SPEC-SEC-VALIDATOR-COVERAGE-001
version: "0.1.0"
status: draft
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-CODEBASE-AUDIT-001 (parent audit, Cluster A)
  - SPEC-SEC-WEBHOOK-001 (validator-env-parity precedent)
---

# SPEC-SEC-VALIDATOR-COVERAGE-001: Fail-closed Settings validators voor 12 auth-secrets

## Summary

Sluit het `fail-open-auth` HIGH pitfall-patroon door 12 missing fail-closed `@model_validator(mode="after")` validators toe te voegen aan pydantic Settings in 4 services. Empty/whitespace-only auth-secrets falen nu silent — een misconfigured deploy disabled auth zonder error. Deze SPEC dwingt fail-fast bij startup.

## Motivation

Per `reports/audit-2026-05-04/security-findings.md` sectie 1: 12 TPs gevonden waar Settings-velden voor auth-secrets geen non-empty validator hebben. Per pitfall `validator-env-parity` (HIGH): elke validator moet eerst de env-var in SOPS hebben staan, anders triggert het 502-cascade bij service-restart.

## Scope

### In scope (12 validators × 4 services)

**klai-portal/backend/app/core/config.py** (8 validators):
- `internal_secret` (mailer→portal Bearer)
- `klai_connector_secret` (portal→connector Bearer)
- `knowledge_ingest_secret` (portal→ingest X-Internal-Secret)
- `retrieval_api_internal_secret` (portal→retrieval)
- `docs_internal_secret` (portal→docs)
- `zitadel_portal_client_secret` (BFF code exchange)
- `portal_secrets_key` + `encryption_key` + `sso_cookie_key` + `bff_session_key` (encryption keys at rest — 4 separate)

**klai-connector/app/core/config.py** (1 validator):
- `portal_caller_secret` (inbound from portal)

**klai-knowledge-ingest/knowledge_ingest/config.py** (1 validator):
- `gitea_webhook_secret` (HMAC verify Gitea push)

**klai-mailer/app/config.py** (1 validator):
- `portal_internal_secret` (mailer→portal outbound)

**klai-retrieval-api/retrieval_api/config.py** (1 validator):
- `portal_internal_secret` (retrieval→portal IdentityAsserter)

### Out of scope

- Reserved-but-unused secrets (`vexa_admin_token`, `docs_internal_secret` in ingest) — separate cleanup
- Secret rotation procedure (covered by SPEC-RESTORE-001 / docs/runbooks/credential-rotation.md)

## Acceptance criteria

1. Per service: voor elk listed secret-veld bestaat `@model_validator(mode="after") _require_<field>` die `ValueError` raises bij empty/whitespace-only
2. Per service: dedicated test `tests/test_config_fail_closed.py` (mailer-style) met `test_settings_startup_fails_without_<field>` voor elke validator
3. Pre-flight: env-var bestaat in `klai-infra/core-01/.env.sops` BEFORE merge (per pitfall `validator-env-parity`)
4. Post-deploy verify: `docker exec klai-core-<service>-1 printenv <VAR>` toont non-empty waarde
5. Geen container-restart-loop op deploy

## Implementation outline

Volg precies het `klai-mailer/app/config.py::_require_webhook_secret` pattern:
```python
@model_validator(mode="after")
def _require_<field>(self) -> "Settings":
    """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-<n>: fail-closed on missing <FIELD>.

    [Why] [Where used] [Failure mode without validator]
    Env-parity: <FIELD> must exist in klai-infra/core-01/.env.sops BEFORE merge.
    """
    if not self.<field> or not self.<field>.strip():
        raise ValueError(
            "Missing required: <FIELD> (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-<n>). "
            "Set it in SOPS before starting <service>."
        )
    return self
```

## Sequencing (one batch per service to limit blast radius)

1. **portal-api batch** — 8 validators in 1 PR + tests; SOPS env-vars (8 vars) in 1 SOPS commit BEFORE PR-merge
2. **klai-connector batch** — 1 validator
3. **klai-knowledge-ingest batch** — 1 validator
4. **klai-mailer batch** — 1 validator
5. **klai-retrieval-api batch** — 1 validator

Per batch: SSH SOPS workflow uit `.claude/rules/klai/infra/sops-env.md`, deploy, verify, then merge code-PR.

## Risks

| Risk | Mitigation |
|---|---|
| Deploy-loop bij missing env-var | Pre-flight grep + verify in SOPS BEFORE merge |
| Per-key rotation impact | Document per validator wat het breekt bij empty value |
| Cross-service trust-boundary breakage tijdens rollout | Sequentiële per-service deploy (één tegelijk) |

## References

- `.claude/rules/klai/pitfalls/process-rules.md` — `fail-open-auth`, `validator-env-parity`, `empty-secret-fail-open`
- `.claude/rules/klai/infra/sops-env.md` — SSH SOPS workflow
- `reports/audit-2026-05-04/security-findings.md` (sectie 1.x)
- `klai-mailer/app/config.py::_require_webhook_secret` — canonical pattern
