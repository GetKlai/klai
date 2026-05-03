"""
Profile ladder for SPEC-PORTAL-PROFILES-001.

Five-rung role hierarchy: personal -> company -> kb_manager -> group_manager -> admin.
Each rung is a strict capability superset of the rung below it.

@MX:ANCHOR fan_in=3+ -- PROFILE_CAPABILITIES is the authoritative capability table.
                         Only contains capability strings checked via require_capability().
                         Direct role checks (org-KB read, groups, billing) use _require_at_least.
@MX:ANCHOR fan_in=3+ -- _require_at_least is the authoritative role gate factory.
                         Replace all _require_admin_or_group_admin* with this.
@MX:ANCHOR fan_in=3+ -- effective_kb_limits is the authoritative quota resolver.
                         profile wins, plan can only lower (REQ-5).
"""

from enum import StrEnum

from fastapi import HTTPException, status

from app.core.plan_limits import KBLimits, get_plan_limits


class Capability(StrEnum):
    """All valid capability strings checked via require_capability().

    Using StrEnum so pyright catches typos at call sites while remaining
    fully compatible with frozenset[str] and set[str] containers at runtime.
    C1: SPEC-PORTAL-PROFILES-001 Phase 1.6.
    """

    KB_CONNECTORS = "kb.connectors"
    KB_CONNECTORS_EXTERNAL = "kb.connectors.external"
    KB_CREATE_ORG = "kb.create_org"
    KB_MEMBERS = "kb.members"
    KB_TAXONOMY = "kb.taxonomy"
    KB_GAPS = "kb.gaps"


PROFILE_LADDER: list[str] = [
    "personal",
    "company",
    "kb_manager",
    "group_manager",
    "admin",
]

# O(1) rank lookups -- use PROFILE_RANK.get(role, -1) instead of PROFILE_LADDER.index(role).
# PROFILE_LADDER is kept for iteration / display; PROFILE_RANK is for ranking comparisons only.
# C2: SPEC-PORTAL-PROFILES-001 Phase 1.6.
PROFILE_RANK: dict[str, int] = {role: idx for idx, role in enumerate(PROFILE_LADDER)}

# Capability strings for SPEC v0.2.0: only capabilities that are actually checked
# via require_capability() on endpoints.  Direct-role checks (org-KB read filter,
# append-via-chat, groups manage, billing, settings) are NOT capability strings.
_KB_BASIC_CAPS: frozenset[str] = frozenset({Capability.KB_CONNECTORS})

_KB_FULL_CAPS: frozenset[str] = _KB_BASIC_CAPS | frozenset(
    {
        Capability.KB_CONNECTORS_EXTERNAL,
        Capability.KB_CREATE_ORG,
        Capability.KB_MEMBERS,
        Capability.KB_TAXONOMY,
        Capability.KB_GAPS,
    }
)

PROFILE_CAPABILITIES: dict[str, frozenset[str]] = {
    "personal": _KB_BASIC_CAPS,
    "company": _KB_BASIC_CAPS,
    "kb_manager": _KB_FULL_CAPS,
    "group_manager": _KB_FULL_CAPS,
    "admin": _KB_FULL_CAPS,
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

# -----------------------------------------------------------------------------
# REQ-5: Role-aware KB quota limits
# profile wins, plan can only lower (min wins everywhere except can_create_org_kbs
# which requires AND).
# -----------------------------------------------------------------------------

# Per-profile quota limits.  None means unlimited within the role.
# personal and company are capped at 5 personal KBs and 20 items per KB.
# kb_manager and above are unlimited and may create org KBs.
PROFILE_LIMITS: dict[str, KBLimits] = {
    "personal": KBLimits(
        max_personal_kbs_per_user=5,
        max_items_per_kb=20,
        can_create_org_kbs=False,
        capabilities=frozenset(),
    ),
    "company": KBLimits(
        max_personal_kbs_per_user=5,
        max_items_per_kb=20,
        can_create_org_kbs=False,
        capabilities=frozenset(),
    ),
    "kb_manager": KBLimits(
        max_personal_kbs_per_user=None,
        max_items_per_kb=None,
        can_create_org_kbs=True,
        capabilities=frozenset(),
    ),
    "group_manager": KBLimits(
        max_personal_kbs_per_user=None,
        max_items_per_kb=None,
        can_create_org_kbs=True,
        capabilities=frozenset(),
    ),
    "admin": KBLimits(
        max_personal_kbs_per_user=None,
        max_items_per_kb=None,
        can_create_org_kbs=True,
        capabilities=frozenset(),
    ),
}

# Most-restrictive fallback for unknown roles.
_FALLBACK_PROFILE_LIMITS = PROFILE_LIMITS["personal"]


def _min_with_unlimited(a: int | None, b: int | None) -> int | None:
    """Return the lower of two limits where None means unlimited.

    None (unlimited) loses to any finite limit: min(None, 5) = 5.
    min(None, None) = None (both unlimited -> still unlimited).
    """
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def effective_kb_limits(role: str, plan: str) -> KBLimits:
    """Return the effective KB quota limits for (role, plan).

    REQ-5: profile wins, plan can only lower.
    - max_personal_kbs_per_user = min(profile_limit, plan_limit)
    - max_items_per_kb          = min(profile_limit, plan_limit)
    - can_create_org_kbs        = profile_allows AND plan_allows

    Examples:
      complete plan + personal role   -> 5/20 (profile lowers)
      core plan    + kb_manager role  -> 5/20 (plan lowers)
      complete plan + kb_manager role -> unlimited
      core plan    + personal role    -> 5/20 (both agree)
    """
    profile_lim = PROFILE_LIMITS.get(role, _FALLBACK_PROFILE_LIMITS)
    plan_lim = get_plan_limits(plan)

    # G4: capabilities = role caps intersected with plan caps (profile wins, plan can only lower).
    role_caps = PROFILE_CAPABILITIES.get(role, frozenset())
    plan_caps = plan_lim.capabilities
    return KBLimits(
        max_personal_kbs_per_user=_min_with_unlimited(
            profile_lim.max_personal_kbs_per_user,
            plan_lim.max_personal_kbs_per_user,
        ),
        max_items_per_kb=_min_with_unlimited(
            profile_lim.max_items_per_kb,
            plan_lim.max_items_per_kb,
        ),
        can_create_org_kbs=(profile_lim.can_create_org_kbs and plan_lim.can_create_org_kbs),
        capabilities=role_caps & plan_caps,
    )


def effective_role(user: object) -> str:
    """Return the effective profile role for the user.

    G5: Returns "personal" (most-restrictive) if user is None.
    """
    if user is None:
        return "personal"
    return str(user.role)  # type: ignore[attr-defined]


def has_capability(user: object, capability: Capability) -> bool:
    """Return True if the user has the given capability based on their role."""
    role = effective_role(user)
    caps = PROFILE_CAPABILITIES.get(role, frozenset())
    return capability in caps


def check_connector_allowed(user: object, connector_type: str) -> None:
    """Raise HTTP 403 if user role is not allowed to use the given connector type.

    REQ-3: personal and company roles may only use url/upload connector types.
    kb_manager and above may use all connector types.

    Checks role-level capability (PROFILE_CAPABILITIES).  Plan ceiling on
    kb.connectors.external is handled inline in create_connector() in app/api/connectors.py.

    @MX:ANCHOR fan_in=2+ -- called from create_connector and check_connector_type.
    """
    role = effective_role(user)
    if role in _BASIC_CONNECTOR_ROLES and connector_type not in _BASIC_CONNECTOR_TYPES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="connector_not_allowed_for_profile",
        )


def _require_at_least(required_role: str):
    """Return a callable that enforces a minimum profile role.

    For direct unit-test invocation, call the returned function with
    caller_user explicitly:
        dep = _require_at_least("group_manager")
        dep(caller_user=some_user)  # raises HTTPException if role too low

    Raises HTTP 403 if the caller role is ranked below required_role.

    @MX:ANCHOR fan_in=3+ -- primary role gate for admin-group endpoints.
    """
    required_idx = PROFILE_RANK.get(required_role, -1)

    def _check(caller_user: object) -> None:
        role = effective_role(caller_user)
        caller_idx = PROFILE_RANK.get(role, -1)
        if caller_idx < required_idx:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {required_role!r} or higher required",
            )

    return _check
