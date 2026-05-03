"""
Profile ladder for SPEC-PORTAL-PROFILES-001.

Five-rung role hierarchy: personal -> company -> kb_manager -> group_manager -> admin.
Each rung is a strict capability superset of the rung below it.

@MX:ANCHOR fan_in=3+ -- PROFILE_CAPABILITIES is the authoritative capability table.
                         Do not add capability checks outside this module.
@MX:ANCHOR fan_in=3+ -- _require_at_least is the authoritative role gate factory.
                         Replace all _require_admin_or_group_admin* with this.
"""

from fastapi import HTTPException, status

PROFILE_LADDER: list[str] = [
    "personal",
    "company",
    "kb_manager",
    "group_manager",
    "admin",
]

# Capability accumulation: each rung includes all capabilities of lower rungs.
# personal (rung 0)
_PERSONAL_CAPS: frozenset[str] = frozenset(
    [
        "kb.create_personal",
        "kb.connectors.url",
        "kb.connectors.upload",
    ]
)

# company adds: read org KB, append via chat
_COMPANY_CAPS: frozenset[str] = _PERSONAL_CAPS | frozenset(
    [
        "kb.read_org",
        "kb.append_via_chat",
    ]
)

# kb_manager adds: external connectors, create org KB, member/taxonomy/gaps management
_KB_MANAGER_CAPS: frozenset[str] = _COMPANY_CAPS | frozenset(
    [
        "kb.connectors.external",
        "kb.create_org",
        "kb.members",
        "kb.taxonomy",
        "kb.gaps",
    ]
)

# group_manager adds: group management
_GROUP_MANAGER_CAPS: frozenset[str] = _KB_MANAGER_CAPS | frozenset(
    [
        "groups.manage",
    ]
)

# admin adds: user invites, billing, org settings
_ADMIN_CAPS: frozenset[str] = _GROUP_MANAGER_CAPS | frozenset(
    [
        "groups.invite_users",
        "org.billing",
        "org.settings",
    ]
)

PROFILE_CAPABILITIES: dict[str, frozenset[str]] = {
    "personal": _PERSONAL_CAPS,
    "company": _COMPANY_CAPS,
    "kb_manager": _KB_MANAGER_CAPS,
    "group_manager": _GROUP_MANAGER_CAPS,
    "admin": _ADMIN_CAPS,
}

# Connector types that are allowed without kb.connectors.external
_BASIC_CONNECTOR_TYPES: frozenset[str] = frozenset(["url", "upload"])

# Roles that may only use basic connector types
_BASIC_CONNECTOR_ROLES: frozenset[str] = frozenset(["personal", "company"])

# Migration map: old role values -> new role values (REQ-11)
ROLE_MIGRATION_MAP: dict[str, str] = {
    "admin": "admin",
    "group-admin": "group_manager",
    "member": "personal",
}


def effective_role(user: object) -> str:
    """Return the effective profile role for the user."""
    return str(user.role)  # type: ignore[attr-defined]


def has_capability(user: object, capability: str) -> bool:
    """Return True if the user has the given capability based on their role."""
    role = effective_role(user)
    caps = PROFILE_CAPABILITIES.get(role, frozenset())
    return capability in caps


def check_connector_allowed(user: object, connector_type: str) -> None:
    """Raise HTTP 403 if user role is not allowed to use the given connector type.

    REQ-3: personal and company roles may only use url/upload connector types.
    kb_manager and above may use all connector types.

    @MX:ANCHOR fan_in=2+ -- called from create_connector and check_connector_type.
    """
    role = effective_role(user)
    if role in _BASIC_CONNECTOR_ROLES and connector_type not in _BASIC_CONNECTOR_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="connector_not_allowed_for_profile",
        )


def _require_at_least(required_role: str):
    """Return a FastAPI dependency that enforces a minimum profile role.

    Usage:
        @router.delete(
            "/groups/{group_id}",
            dependencies=[Depends(_require_at_least("group_manager"))],
        )

    Raises HTTP 403 if the caller role is ranked below required_role.

    @MX:ANCHOR fan_in=3+ -- primary role gate for admin-group endpoints.
    """
    required_idx = PROFILE_LADDER.index(required_role)

    def _check(caller_user: object = None) -> None:
        role = effective_role(caller_user)
        caller_idx = PROFILE_LADDER.index(role) if role in PROFILE_LADDER else -1
        if caller_idx < required_idx:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {required_role!r} or higher required",
            )

    return _check
