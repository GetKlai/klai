"""Auto-seed dev org and user for AUTH_DEV_MODE.

Only runs when settings.is_auth_dev_mode is True. Creates a minimal
portal_orgs + portal_users row so the backend can start against an
empty database without manual SQL.

Safety: requires BOTH debug=True AND auth_dev_mode=True. The production
validator (_no_debug_in_production) blocks debug=True when PORTAL_ENV=production.
"""

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

DEV_ORG_ZITADEL_ID = "dev-org-1"
DEV_ORG_NAME = "Dev Organization"
DEV_ORG_SLUG = "dev"

DEV_USER_EMAIL = "dev@klai.local"
DEV_USER_DISPLAY_NAME = "Dev User"


async def ensure_dev_user_exists(db: AsyncSession, dev_user_id: str) -> None:
    """Create dev org + user if they don't exist. Idempotent via ON CONFLICT."""

    # Use raw SQL to avoid RLS complications — this runs at startup before
    # any tenant context is set, on the superuser connection.
    result = await db.execute(
        text(
            """
            INSERT INTO portal_orgs (zitadel_org_id, name, slug, plan, provisioning_status)
            VALUES (:org_id, :name, :slug, 'professional', 'complete')
            ON CONFLICT (zitadel_org_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "org_id": DEV_ORG_ZITADEL_ID,
            "name": DEV_ORG_NAME,
            "slug": DEV_ORG_SLUG,
        },
    )
    new_org = result.scalar_one_or_none()

    if new_org is not None:
        org_id = new_org
        logger.info("dev_seed_org_created", org_id=org_id, slug=DEV_ORG_SLUG)
    else:
        # Org already exists — fetch its id
        row = await db.execute(
            text("SELECT id FROM portal_orgs WHERE zitadel_org_id = :org_id"),
            {"org_id": DEV_ORG_ZITADEL_ID},
        )
        org_id = row.scalar_one()
        logger.info("dev_seed_org_exists", org_id=org_id, slug=DEV_ORG_SLUG)

    # Insert dev user
    user_result = await db.execute(
        text(
            """
            INSERT INTO portal_users (zitadel_user_id, org_id, role, display_name, email, status)
            VALUES (:user_id, :org_id, 'admin', :display_name, :email, 'active')
            ON CONFLICT (zitadel_user_id, org_id) DO NOTHING
            RETURNING id
            """
        ),
        {
            "user_id": dev_user_id,
            "org_id": org_id,
            "display_name": DEV_USER_DISPLAY_NAME,
            "email": DEV_USER_EMAIL,
        },
    )
    new_user = user_result.scalar_one_or_none()

    if new_user is not None:
        logger.info("dev_seed_user_created", user_id=dev_user_id, org_slug=DEV_ORG_SLUG)
    else:
        logger.info("dev_seed_user_exists", user_id=dev_user_id, org_slug=DEV_ORG_SLUG)

    await db.commit()
