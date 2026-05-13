"""System group registry.

SPEC-PORTAL-RBAC-001 v0.2.0 + SPEC-PORTAL-EXTENSIONS-UNIFY-001: empty
registry. Role-bind and add-on groups are removed -- profile is the single
writer of `portal_users.role`, and products are derived from
`portal_orgs.platform_unlocked_features` + profile rank. The
`create_system_groups` helper remains as a no-op so the provisioning state
machine keeps its API surface stable.
"""

from sqlalchemy.ext.asyncio import AsyncSession

# Empty by design -- see SPEC-PORTAL-RBAC-001 v0.2.0.
SYSTEM_GROUPS: list[dict] = []

# Empty mapping -- preserved name so callers that still import it don't break.
SYSTEM_GROUP_ROLE_MAP: dict[str, str] = {}


async def create_system_groups(org_id: int, db: AsyncSession) -> None:
    """No-op: system groups are removed by SPEC-PORTAL-RBAC-001.

    Kept as a stable contract for `provisioning/state_machine.py` so the
    provisioning flow continues to call into this module without branching.
    """
    _ = (org_id, db)  # parameters retained for contract stability
