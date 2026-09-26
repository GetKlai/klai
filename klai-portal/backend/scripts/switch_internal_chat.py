"""Switch one tenant's internal chat (LibreChat) between the LiteLLM hook and portal-api.

docs/architecture/chat-quality-history-and-plan.md §7.3: the internal chat moves
to the one chat pipeline tenant by tenant. The "Klai AI" endpoint in
deploy/librechat/librechat.yaml reads its key and base URL from two variables in
the tenant's LibreChat .env, so a switch rewrites exactly those two lines and
recreates the container:

- ``portal``: KLAI_CHAT_API_KEY becomes the org's internal-chat partner key
  (reused when the .env already holds it, minted otherwise) and
  KLAI_CHAT_BASE_URL becomes portal-api's partner endpoint.
- ``litellm``: both go back to LITELLM_API_KEY from the same file and LiteLLM's
  URL, and the internal-chat key is revoked, so no copy nobody holds stays valid.

Recreate, not restart: Docker bakes the .env into the container's environment
at create time and LibreChat's dotenv does not override it, so a restart would
keep the old endpoint (same reason as the MCP apply path in app/api/mcp_servers.py).
A compose-managed container (librechat-getklai) belongs to compose; the script
writes its .env, prints the host command and exits 3.

Run inside portal-api, or in a throwaway clone of it:

    docker exec klai-core-portal-api-1 python scripts/switch_internal_chat.py <slug> portal|litellm [--rotate]
    deploy/scripts/portal-api-oneoff.sh -- python scripts/switch_internal_chat.py <slug> portal|litellm

``--rotate`` replaces an internal-chat key that exists in the database but not in
this .env (e.g. after an interrupted run). The key is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    # Same bootstrap as the other operator scripts: running "python scripts/x.py"
    # puts scripts/ on the path, not the backend root, so ``app`` would not import.
    sys.path.insert(0, str(BACKEND_ROOT))

import docker  # noqa: E402
import docker.errors  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.internal import _is_compose_managed  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import cross_org_session, tenant_scoped_session  # noqa: E402
from app.core.provisioning_names import validate_slug_for_provisioning  # noqa: E402
from app.models.portal import PortalOrg  # noqa: E402
from app.services.internal_chat_keys import (  # noqa: E402
    holds_internal_chat_key,
    mint_internal_chat_key,
    revoke_internal_chat_key,
)
from app.services.provisioning.generators import _ENV_KEY_RE, LITELLM_CHAT_BASE_URL  # noqa: E402
from app.services.provisioning.infrastructure import (  # noqa: E402
    _invalidate_librechat_config_cache,
    _read_dotenv_file,
    _start_librechat_container,
)

PORTAL_CHAT_BASE_URL = "http://portal-api:8010/partner/v1"
EXIT_COMPOSE_RECREATE_PENDING = 3


def rewrite_env(content: str, values: Mapping[str, str]) -> str:
    """Replace the ``KEY=`` lines named in ``values``; every other byte stays as it was.

    A key that is not in the file raises instead of being appended: the fleet
    backfill (/internal/librechat/regenerate) writes both chat variables, and a
    tenant without them has not had it. An empty value raises too, because
    librechat.yaml would expand it into an empty API key.
    """
    empty = sorted(key for key, value in values.items() if not value)
    if empty:
        raise ValueError(f"refusing to write an empty value for {empty}")
    seen: set[str] = set()
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _ENV_KEY_RE.match(line)
        if match and match.group(1) in values:
            key = match.group(1)
            lines[index] = f"{key}={values[key]}" + ("\n" if line.endswith("\n") else "")
            seen.add(key)
    missing = sorted(set(values) - seen)
    if missing:
        raise ValueError(f"{missing} not in the tenant .env; run the LibreChat env backfill first")
    return "".join(lines)


def _write_env(path: Path, content: str) -> None:
    """Replace the .env atomically; the file holds the tenant's keys, so it is 0600 from its first byte."""
    tmp = path.with_name(f"{path.name}.switch")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    os.replace(tmp, path)
    path.chmod(0o600)


async def _load_org(slug: str) -> PortalOrg:
    # The only cross-org read: the org id has to be known before a tenant scope can be set.
    async with cross_org_session() as session:
        org = (
            await session.execute(select(PortalOrg).where(PortalOrg.slug == slug, PortalOrg.deleted_at.is_(None)))
        ).scalar_one_or_none()
    if org is None:
        raise SystemExit(f"No active org with slug {slug}.")
    return org


def _compose_managed(slug: str) -> bool:
    name = validate_slug_for_provisioning(slug, domain=settings.domain).librechat_container
    try:
        return _is_compose_managed(docker.from_env().containers.get(name))
    except docker.errors.NotFound:
        return False


def _recreate(org: PortalOrg) -> None:
    _invalidate_librechat_config_cache(org.slug)
    _start_librechat_container(
        org.slug,
        f"{settings.librechat_host_data_path}/{org.slug}/.env",
        org.mcp_servers or None,
        rollback_on_failure=True,
    )


async def revoke(slug: str) -> int:
    """Delete the org's internal-chat key once its LibreChat no longer holds it (compose tenants, after the recreate)."""
    org = await _load_org(slug)
    async with tenant_scoped_session(org.id) as db:
        revoked = await revoke_internal_chat_key(db, org.id)
    print(f"{org.slug}: revoked {revoked} internal-chat key(s)")
    return 0


async def switch(slug: str, target: str, *, rotate: bool = False) -> int:
    org = await _load_org(slug)
    env_path = Path(settings.librechat_container_data_path) / org.slug / ".env"
    env = _read_dotenv_file(env_path)
    if not {"KLAI_CHAT_API_KEY", "KLAI_CHAT_BASE_URL"} <= env.keys():
        raise SystemExit(f"{env_path} lacks the chat variables; run the LibreChat env backfill first.")
    compose_managed = await asyncio.to_thread(_compose_managed, org.slug)

    if target == "portal":
        current = env.get("KLAI_CHAT_API_KEY", "")
        async with tenant_scoped_session(org.id) as db:
            if await holds_internal_chat_key(db, org.id, current):
                key, key_action = current, "reused the internal-chat key this .env already holds"
            else:
                key = await mint_internal_chat_key(db, org.id, rotate=rotate)
                if key is None:
                    raise SystemExit(
                        f"{org.slug} has an internal-chat key this .env does not hold; "
                        "rerun with --rotate to replace it. Nothing was changed."
                    )
                key_action = "minted a new internal-chat key" + (" (old one deleted)" if rotate else "")
        values = {"KLAI_CHAT_API_KEY": key, "KLAI_CHAT_BASE_URL": PORTAL_CHAT_BASE_URL}
    else:
        values = {"KLAI_CHAT_API_KEY": env.get("LITELLM_API_KEY", ""), "KLAI_CHAT_BASE_URL": LITELLM_CHAT_BASE_URL}
        key_action = "KLAI_CHAT_API_KEY set to this tenant's LITELLM_API_KEY"

    _write_env(env_path, rewrite_env(env_path.read_text(), values))
    print(f"{org.slug}: {key_action}; KLAI_CHAT_BASE_URL={values['KLAI_CHAT_BASE_URL']} written to {env_path}")

    if compose_managed:
        container = validate_slug_for_provisioning(org.slug, domain=settings.domain).librechat_container
        print(f"{container} is compose-managed and was NOT recreated. On the host run:")
        print(f"  cd /opt/klai && docker compose up -d --no-deps --force-recreate {container}")
        if target == "litellm":
            # The running container still authenticates with the internal key
            # until the recreate; revoking it now would give every turn a 401.
            print(f"  then: python scripts/switch_internal_chat.py {org.slug} revoke")
        return EXIT_COMPOSE_RECREATE_PENDING

    await asyncio.to_thread(_recreate, org)
    print(f"{org.slug}: LibreChat container recreated and healthy on {target}")
    if target == "litellm":
        await revoke(org.slug)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slug")
    parser.add_argument("target", choices=["portal", "litellm", "revoke"])
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    if args.target == "revoke":
        sys.exit(asyncio.run(revoke(args.slug)))
    sys.exit(asyncio.run(switch(args.slug, args.target, rotate=args.rotate)))
