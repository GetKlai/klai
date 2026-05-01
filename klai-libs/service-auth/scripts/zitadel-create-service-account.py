"""Create a Zitadel service account for a Klai internal service.

SPEC-SEC-SERVICE-AUTH-001 REQ-6 (idempotent bootstrap).

Usage::

    export ZITADEL_ADMIN_PAT=$(
        ssh core-01 "sudo grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env | cut -d= -f2-"
    )
    export ZITADEL_INSTANCE_URL=https://auth.getklai.com
    export ZITADEL_PLATFORM_ORG_ID=362757920133283846
    python zitadel-create-service-account.py --name svc-litellm

Output: writes the freshly minted client_secret to a temp file
``./.<name>-secret.txt`` (mode 0600). The operator MUST then encrypt that
value into ``klai-infra/core-01/.env.sops`` and DELETE the temp file.

Idempotency: re-running with the same ``--name`` reuses the existing user
and reports its userId. The script does NOT auto-rotate the secret — that
is a separate ``rotate-service-account.py`` concern.

This script does NOT auto-grant scopes. Scope assignment in Zitadel is a
separate concept (project roles + audience claims) and is documented in
the SPEC's Phase A runbook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx


def _log(message: str, **kv: Any) -> None:
    """Stderr line so stdout stays parsable for piping if needed."""
    fields = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"[zitadel-bootstrap] {message} {fields}".rstrip(), file=sys.stderr)


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"error: ${name} is required")
    return val


def _api(
    *,
    method: str,
    url: str,
    pat: str,
    org_id: str | None = None,
    json_body: dict | None = None,
) -> dict:
    headers = {
        "Authorization": f"Bearer {pat}",
        "Content-Type": "application/json",
    }
    if org_id:
        headers["X-Zitadel-Orgid"] = org_id

    resp = httpx.request(method, url, headers=headers, json=json_body, timeout=15.0)
    if resp.status_code >= 400:
        sys.exit(f"error: Zitadel {method} {url} → {resp.status_code}\nbody: {resp.text[:500]}")
    if resp.text:
        return resp.json()
    return {}


def find_existing_user(*, instance_url: str, pat: str, org_id: str, name: str) -> str | None:
    """Return userId if a service account with this name exists, else None.

    Uses the Zitadel v2 user search endpoint. Searches by username field.
    """
    body = {
        "queries": [
            {"userNameQuery": {"userName": name, "method": "TEXT_QUERY_METHOD_EQUALS"}},
        ],
    }
    resp = _api(
        method="POST",
        url=f"{instance_url}/v2/users",
        pat=pat,
        org_id=org_id,
        json_body=body,
    )
    users = resp.get("result") or []
    if not users:
        return None
    user_id = users[0].get("userId")
    if not user_id:
        return None
    _log("service_account_exists", name=name, user_id=user_id)
    return user_id


def create_machine_user(*, instance_url: str, pat: str, org_id: str, name: str) -> str:
    """Create a Zitadel machine user (service account) with the given username.

    Returns: userId.
    """
    body = {
        "userName": name,
        "machine": {
            "name": name,
            "description": f"Klai internal service account ({name}) per SPEC-SEC-SERVICE-AUTH-001",
            # JWT access tokens (vs opaque) so receivers can validate locally
            # against Zitadel JWKS instead of round-tripping introspection.
            "accessTokenType": "ACCESS_TOKEN_TYPE_JWT",
        },
    }
    resp = _api(
        method="POST",
        url=f"{instance_url}/v2/users/machine",
        pat=pat,
        org_id=org_id,
        json_body=body,
    )
    user_id: str = resp["userId"]
    _log("service_account_created", name=name, user_id=user_id)
    return user_id


def generate_client_secret(*, instance_url: str, pat: str, org_id: str, user_id: str) -> str:
    """Generate (or replace) the client_secret for a machine user.

    Returns: the plaintext secret, which is shown ONCE — Zitadel never
    surfaces it again. Caller is responsible for getting it into SOPS
    immediately and deleting any plaintext copy.
    """
    resp = _api(
        method="PUT",
        url=f"{instance_url}/v2/users/{user_id}/secret",
        pat=pat,
        org_id=org_id,
    )
    client_secret: str = resp["clientSecret"]
    _log("client_secret_generated", user_id=user_id, hint=f"{client_secret[:6]}...")
    return client_secret


def write_secret_file(*, name: str, client_id: str, client_secret: str) -> Path:
    """Write the secret to a 0600 temp file next to the script.

    Returns: path to the written file. Operator deletes it after SOPS encryption.
    """
    out = Path(f".{name}-secret.txt").resolve()
    payload = {
        "service_account_name": name,
        "client_id": client_id,
        "client_secret": client_secret,
        "instructions": (
            f"Encrypt these into klai-infra/core-01/.env.sops as:\n"
            f"  KLAI_{name.upper().replace('-', '_').removeprefix('SVC_')}_CLIENT_ID="
            f"{client_id}\n"
            f"  KLAI_{name.upper().replace('-', '_').removeprefix('SVC_')}_CLIENT_SECRET="
            f"{client_secret}\n"
            f"Then DELETE this file.\n"
        ),
    }
    out.write_text(json.dumps(payload, indent=2))
    out.chmod(0o600)
    _log("secret_written", path=str(out), mode="0600")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name",
        required=True,
        help="Service account username (convention: 'svc-<servicename>')",
    )
    args = parser.parse_args()

    if not args.name.startswith("svc-"):
        sys.exit("error: --name must follow the 'svc-<servicename>' convention")

    pat = _require_env("ZITADEL_ADMIN_PAT")
    instance_url = _require_env("ZITADEL_INSTANCE_URL")
    org_id = _require_env("ZITADEL_PLATFORM_ORG_ID")

    user_id = find_existing_user(instance_url=instance_url, pat=pat, org_id=org_id, name=args.name)
    if user_id is None:
        user_id = create_machine_user(
            instance_url=instance_url, pat=pat, org_id=org_id, name=args.name
        )

    # In Zitadel, the client_id IS the userId for machine users using
    # Client Credentials grant. The client_secret is generated separately.
    client_id = user_id
    client_secret = generate_client_secret(
        instance_url=instance_url, pat=pat, org_id=org_id, user_id=user_id
    )

    secret_path = write_secret_file(
        name=args.name,
        client_id=client_id,
        client_secret=client_secret,
    )

    print(
        f"\nDone. Service account: {args.name}\n"
        f"  client_id: {client_id}\n"
        f"  secret file: {secret_path}\n\n"
        f"NEXT STEPS:\n"
        f"  1. Encrypt the secret into klai-infra/core-01/.env.sops "
        f"(see file contents).\n"
        f"  2. Push klai-infra changes; CI workflow will sync to core-01.\n"
        f"  3. DELETE {secret_path} after SOPS encryption succeeds.\n"
        f"  4. Continue with SPEC-SEC-SERVICE-AUTH-001 Phase B (receiver) "
        f"and Phase C-1 (caller).\n"
    )


if __name__ == "__main__":
    main()
