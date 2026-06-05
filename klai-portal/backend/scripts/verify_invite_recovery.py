"""Verify customer invite recovery state after resending invites.

Read-only support check for the production bug where active/offboarded users
could be stuck behind an expired/invalid invite link.

Usage, inside the portal-api environment:
    uv run python -m scripts.verify_invite_recovery \\
        --org-slug mijndomein-37476129 \\
        colleague1@example.com colleague2@example.com colleague3@example.com

The script exits non-zero when any supplied email is not ready for the customer
to retry login.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from klai_kb_slugs import personal_kb_slug
from sqlalchemy import select


@dataclass
class InviteRecoveryCheck:
    email: str
    ok: bool = False
    org_id: int | None = None
    org_slug: str | None = None
    zitadel_user_id: str | None = None
    zitadel_state: str | None = None
    portal_status: str | None = None
    portal_role: str | None = None
    personal_kb_exists: bool = False
    failures: list[str] = field(default_factory=list)


def _extract_zitadel_state(response: dict[str, Any]) -> str | None:
    user = response.get("user")
    payload = user if isinstance(user, dict) else response
    state = payload.get("state") if isinstance(payload, dict) else None
    return state if isinstance(state, str) and state else None


async def _load_org(db: Any, *, org_id: int | None, org_slug: str | None) -> Any:
    from app.models.portal import PortalOrg

    if org_id is not None:
        result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    else:
        result = await db.execute(select(PortalOrg).where(PortalOrg.slug == org_slug))
    return result.scalar_one_or_none()


async def _check_email(*, org: Any, email: str) -> InviteRecoveryCheck:
    from app.core.database import AsyncSessionLocal, set_tenant
    from app.models.knowledge_bases import PortalKnowledgeBase
    from app.models.portal import PortalUser
    from app.services.zitadel import zitadel

    check = InviteRecoveryCheck(email=email, org_id=org.id, org_slug=org.slug)

    try:
        check.zitadel_user_id = await zitadel.find_user_id_by_email(email)
    except Exception as exc:
        check.failures.append(f"Zitadel email lookup failed: {exc}")
        return check

    if not check.zitadel_user_id:
        check.failures.append("No Zitadel user found for email")
        return check

    try:
        check.zitadel_state = _extract_zitadel_state(await zitadel.get_user_by_id(check.zitadel_user_id))
    except Exception as exc:
        check.failures.append(f"Zitadel user state lookup failed: {exc}")

    async with AsyncSessionLocal() as db:
        await set_tenant(db, org.id)
        membership_result = await db.execute(
            select(PortalUser).where(
                PortalUser.org_id == org.id,
                PortalUser.zitadel_user_id == check.zitadel_user_id,
            )
        )
        membership = membership_result.scalar_one_or_none()
        if membership is None:
            check.failures.append("No portal membership in target org")
        else:
            check.portal_status = membership.status
            check.portal_role = membership.role

        kb_result = await db.execute(
            select(PortalKnowledgeBase.id).where(
                PortalKnowledgeBase.org_id == org.id,
                PortalKnowledgeBase.slug == personal_kb_slug(check.zitadel_user_id),
                PortalKnowledgeBase.owner_type == "user",
                PortalKnowledgeBase.owner_user_id == check.zitadel_user_id,
            )
        )
        check.personal_kb_exists = kb_result.scalar_one_or_none() is not None

    if check.zitadel_state == "USER_STATE_INITIAL":
        check.failures.append("Zitadel user is stuck in USER_STATE_INITIAL")
    elif check.zitadel_state != "USER_STATE_ACTIVE":
        check.failures.append(f"Zitadel user is not active: {check.zitadel_state or 'unknown'}")

    if check.portal_status != "active":
        check.failures.append(f"Portal membership is not active: {check.portal_status or 'missing'}")

    if not check.personal_kb_exists:
        check.failures.append("Personal knowledge base is missing")

    check.ok = not check.failures
    return check


async def amain(args: argparse.Namespace) -> int:
    from app.core.database import AsyncSessionLocal

    if args.org_id is None and args.org_slug is None:
        print("Either --org-id or --org-slug is required.", file=sys.stderr)
        return 2

    async with AsyncSessionLocal() as db:
        org = await _load_org(db, org_id=args.org_id, org_slug=args.org_slug)

    if org is None:
        print("Target org not found.", file=sys.stderr)
        return 1

    checks = [await _check_email(org=org, email=email) for email in args.emails]

    if args.json:
        print(json.dumps([asdict(check) for check in checks], indent=2, sort_keys=True))
    else:
        for check in checks:
            prefix = "OK" if check.ok else "FAIL"
            print(
                f"{prefix} {check.email}: "
                f"user_id={check.zitadel_user_id or '-'} "
                f"zitadel_state={check.zitadel_state or '-'} "
                f"portal_status={check.portal_status or '-'} "
                f"personal_kb={'yes' if check.personal_kb_exists else 'no'}"
            )
            for failure in check.failures:
                print(f"  - {failure}")

    return 0 if all(check.ok for check in checks) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("emails", nargs="+", help="Customer user email address(es) to verify")
    parser.add_argument("--org-id", type=int, help="Portal org id")
    parser.add_argument("--org-slug", help="Portal org slug, e.g. mijndomein-37476129")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return asyncio.run(amain(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
