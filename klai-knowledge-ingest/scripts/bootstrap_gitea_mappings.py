"""SPEC-TI-007 / C-1: Bootstrap knowledge.gitea_repo_to_org from Gitea.

One-shot script to populate the trusted org-id mapping table by reading
the Gitea API.  Should be run ONCE after the post-deploy RLS SQL has been
applied and before the first webhook arrives.

Usage (on core-01 after alembic upgrade + RLS SQL applied):
    docker exec klai-core-klai-knowledge-ingest-1 \
        python scripts/bootstrap_gitea_mappings.py

The script is IDEMPOTENT: INSERT ... ON CONFLICT DO NOTHING.
Existing mappings are never overwritten; only missing ones are added.

[DRAFT] The exact set of Gitea orgs to enumerate depends on the naming
convention used in the running cluster.  The script assumes:
  - Every Gitea org whose name starts with "org-" is a tenant org.
  - The org description field contains the Zitadel org_id (legacy mapping
    that this SPEC replaces -- used only for the one-time bootstrap).
  - Repos inside each org: all repos are mapped as "{org}/{repo}".

If the description field is already cleared or unset, the operator must
supply the org_id manually:
    INSERT INTO knowledge.gitea_repo_to_org (full_name, org_id)
    VALUES ('org-myslug/mykb', 'zitadel-org-id-here')
    ON CONFLICT DO NOTHING;
"""

import asyncio
import os
import sys

import asyncpg
import httpx


async def main() -> None:
    gitea_url = os.environ.get("GITEA_URL", "http://gitea:3000")
    gitea_token = os.environ.get("GITEA_TOKEN", "")
    postgres_dsn = os.environ.get(
        "POSTGRES_DSN",
        os.environ.get("DATABASE_URL", ""),
    )

    if not postgres_dsn:
        print("ERROR: POSTGRES_DSN or DATABASE_URL must be set", file=sys.stderr)
        sys.exit(1)

    # asyncpg doesn't accept the SQLAlchemy +asyncpg prefix
    pg_url = postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")

    print(f"Connecting to Gitea at {gitea_url}")
    headers = {"Authorization": f"token {gitea_token}"} if gitea_token else {}

    inserted = 0
    skipped = 0

    async with asyncpg.create_pool(pg_url) as pool:
        async with httpx.AsyncClient(
            base_url=gitea_url, headers=headers, timeout=10.0
        ) as client:
            page = 1
            while True:
                resp = await client.get(
                    "/api/v1/admin/orgs",
                    params={"limit": 50, "page": page},
                )
                if resp.status_code != 200:
                    print(f"Gitea /admin/orgs returned {resp.status_code}", file=sys.stderr)
                    break

                orgs = resp.json()
                if not orgs:
                    break

                for org in orgs:
                    org_name = org.get("username", "")
                    if not org_name.startswith("org-"):
                        continue

                    # Legacy: description field holds the Zitadel org_id.
                    zitadel_org_id = (org.get("description") or "").strip()
                    if not zitadel_org_id:
                        print(
                            f"  SKIP {org_name}: no org_id in description field"
                        )
                        skipped += 1
                        continue

                    # Enumerate repos inside this org
                    repo_page = 1
                    while True:
                        rresp = await client.get(
                            f"/api/v1/orgs/{org_name}/repos",
                            params={"limit": 50, "page": repo_page},
                        )
                        if rresp.status_code != 200:
                            break
                        repos = rresp.json()
                        if not repos:
                            break

                        for repo in repos:
                            full_name = repo.get("full_name", "")
                            if not full_name:
                                continue
                            await pool.execute(
                                """
                                INSERT INTO knowledge.gitea_repo_to_org (full_name, org_id)
                                VALUES ($1, $2)
                                ON CONFLICT (full_name) DO NOTHING
                                """,
                                full_name,
                                zitadel_org_id,
                            )
                            print(f"  MAPPED {full_name} -> {zitadel_org_id}")
                            inserted += 1

                        repo_page += 1

                page += 1

    print(f"\nDone: {inserted} repos mapped, {skipped} orgs skipped (no org_id in description).")


if __name__ == "__main__":
    asyncio.run(main())
