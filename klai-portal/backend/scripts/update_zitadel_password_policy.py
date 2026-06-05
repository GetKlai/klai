"""Set and verify Klai's Zitadel password-complexity policy.

Default mode is read-only. Pass ``--apply`` to update Zitadel first.
Requires an IAM/Admin PAT in ``ZITADEL_ADMIN_PAT``; do not use browser tokens.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx

TARGET_POLICY: dict[str, str | bool] = {
    "minLength": "15",
    "hasUppercase": False,
    "hasLowercase": False,
    "hasNumber": False,
    "hasSymbol": False,
}


def _extract_policy(payload: dict[str, Any]) -> dict[str, Any]:
    policy = payload.get("policy")
    return policy if isinstance(policy, dict) else payload


def _policy_errors(payload: dict[str, Any]) -> list[str]:
    policy = _extract_policy(payload)
    errors: list[str] = []
    for key, expected in TARGET_POLICY.items():
        actual = policy.get(key)
        if key == "minLength":
            matches = str(actual) == str(expected)
        else:
            if actual is None and expected is False:
                actual = False
            matches = actual is expected
        if not matches:
            errors.append(f"{key}: expected {expected!r}, got {actual!r}")
    return errors


def _client(base_url: str, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("ZITADEL_BASE_URL", "https://auth.getklai.com"))
    parser.add_argument("--apply", action="store_true", help="Update Zitadel before verifying")
    args = parser.parse_args()

    token = os.getenv("ZITADEL_ADMIN_PAT")
    if not token:
        print("ERROR: ZITADEL_ADMIN_PAT is required", file=sys.stderr)
        return 2

    with _client(args.base_url, token) as client:
        before = client.get("/admin/v1/policies/password/complexity")
        before.raise_for_status()
        before_errors = _policy_errors(before.json())

        if before_errors and not args.apply:
            print("DRIFT: Zitadel password policy is not Klai-compatible:")
            for error in before_errors:
                print(f"- {error}")
            print("Run again with --apply to update it.")
            return 1

        if args.apply:
            response = client.put("/admin/v1/policies/password/complexity", json=TARGET_POLICY)
            response.raise_for_status()

        after = client.get("/admin/v1/policies/password/complexity")
        after.raise_for_status()
        after_errors = _policy_errors(after.json())
        if after_errors:
            print("ERROR: Zitadel password policy still drifts after verification:", file=sys.stderr)
            for error in after_errors:
                print(f"- {error}", file=sys.stderr)
            return 1

    print("OK: Zitadel password policy is Klai-compatible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
