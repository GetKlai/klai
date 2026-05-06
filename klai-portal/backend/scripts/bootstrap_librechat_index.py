"""bootstrap_librechat_index.py

One-shot script to seed portal_users_librechat_index from existing tenant Mongo containers.

SPEC-TI-010C B-6 operator step:
    docker exec klai-core-portal-api-1 python /app/scripts/bootstrap_librechat_index.py

Prerequisites:
- LIBRECHAT_MONGO_ROOT_URI env var must be set
- Database must be migrated (alembic upgrade head, revision a1b2c3d4e5f6)

The script walks every portal_org with a librechat_container, queries the LibreChat
"users" MongoDB collection, extracts the Zitadel user ID (openidId / openid_id / sub),
and upserts a row into portal_users_librechat_index.

Rows that already exist (ON CONFLICT DO NOTHING) are skipped safely.
"""

import asyncio
import os
import sys

import asyncpg
from motor.motor_asyncio import AsyncIOMotorClient

DATABASE_URL = os.environ["DATABASE_URL"]
MONGO_URI = os.environ["LIBRECHAT_MONGO_ROOT_URI"]


async def main() -> None:
    pg = await asyncpg.connect(DATABASE_URL)
    orgs = await pg.fetch(
        "SELECT id, zitadel_org_id, librechat_container FROM portal_orgs "
        "WHERE librechat_container IS NOT NULL AND deleted_at IS NULL"
    )
    print(f"Found {len(orgs)} orgs with librechat_container")

    total_inserted = 0
    total_skipped = 0

    for org in orgs:
        org_id = org["id"]
        container = org["librechat_container"]
        print(f"Processing org {org_id} container={container}")

        mongo = AsyncIOMotorClient(MONGO_URI)
        try:
            cursor = mongo[container]["users"].find({}, {"openidId": 1, "openid_id": 1, "sub": 1})
            async for doc in cursor:
                zid = (
                    doc.get("openidId") or doc.get("openid_id") or doc.get("sub")
                )
                if not zid:
                    continue
                oid = str(doc["_id"])
                # Resolve portal user to confirm they exist
                portal_uid = await pg.fetchval(
                    "SELECT id FROM portal_users WHERE zitadel_user_id = $1 AND org_id = $2",
                    zid, org_id,
                )
                if portal_uid is None:
                    print(f"  WARN: no portal user for zitadel_user_id={zid} in org {org_id}")
                    continue
                result = await pg.execute(
                    "INSERT INTO portal_users_librechat_index "
                    "(librechat_object_id, org_id, zitadel_user_id) "
                    "VALUES ($1, $2, $3) ON CONFLICT (librechat_object_id) DO NOTHING",
                    oid, org_id, zid,
                )
                if result == "INSERT 0 1":
                    total_inserted += 1
                else:
                    total_skipped += 1
        finally:
            mongo.close()

    await pg.close()
    print(f"Done. inserted={total_inserted} skipped={total_skipped}")


if __name__ == "__main__":
    asyncio.run(main())

