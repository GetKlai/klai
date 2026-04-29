# Positive fixtures for .semgrep/rules/jwt-peek-without-verify.yml
#
# Each function below contains an unverified peek-decode that is NOT
# followed by any verified decode. The rule MUST fire on every function.
# These are the regressions the rule is designed to catch — namely a
# future refactor that drops step 3 of the HKDF-per-tenant pattern.
#
# Run the rule against this file via:
#   semgrep --config .semgrep/rules/jwt-peek-without-verify.yml \
#     .semgrep/tests/jwt_peek_positive.py
# Expected: 3 findings (one per function below).
#
# This file is fixture-only. Do not import from production code.

from __future__ import annotations

from typing import Any

import jwt


# ---------------------------------------------------------------------------
# Regression 1: someone deleted the verified decode entirely.
# Rule MUST fire — the unverified payload is consumed as if it were
# authenticated.
# ---------------------------------------------------------------------------
def regression_no_verify_at_all(token: str) -> dict[str, Any]:
    unverified = jwt.decode(token, options={"verify_signature": False})
    org_id = unverified.get("org_id", 0)
    return {"org_id": org_id, "claims": unverified}


# ---------------------------------------------------------------------------
# Regression 2: only an early-return error check on the unverified payload,
# no follow-up verified decode. Rule MUST fire.
# ---------------------------------------------------------------------------
def regression_only_error_check(token: str) -> int:
    unverified = jwt.decode(token, options={"verify_signature": False})
    org_id = unverified.get("org_id", 0)
    if not org_id:
        raise ValueError("missing org_id")
    return org_id


# ---------------------------------------------------------------------------
# Regression 3: a verify_signature=False sandwiched between unrelated work,
# but no verified decode. Rule MUST fire even when the suspect line is
# not the last statement.
# ---------------------------------------------------------------------------
def regression_with_distractors(token: str, audit_log: list[str]) -> dict[str, Any]:
    audit_log.append("entering decode")
    unverified = jwt.decode(token, options={"verify_signature": False})
    audit_log.append("decoded peek")
    audit_log.append(f"org_id={unverified.get('org_id')}")
    return unverified
