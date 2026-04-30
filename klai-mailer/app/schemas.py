"""Per-template Pydantic v2 schemas for `/internal/send`.

SPEC-SEC-MAILER-INJECTION-001 REQ-2. The schema registry is the single
authoritative list of accepted variables per internal template. Adding a
variable to a template MUST include a schema change in the same commit
(REQ-2.5).

All models use `extra="forbid"` — unknown keys raise ValidationError. This
is the first layer of the defence-in-depth that REQ-1 (Jinja2 sandbox) and
REQ-1.3 (StrictUndefined) then reinforce.

@MX:NOTE: TEMPLATE_SCHEMAS is the single source of truth for the accepted
variable surface. Do NOT thread attacker-controlled values around this
registry.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, HttpUrl


class _BaseVars(BaseModel):
    """Shared config: forbid unknowns, strip whitespace on str fields."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class JoinRequestAdminVars(_BaseVars):
    """Variables for the `join_request_admin` email.

    REQ-3.1: `org_id` is REQUIRED so klai-mailer can resolve the expected
    recipient via portal-api. The portal-api caller at
    `klai-portal/backend/app/services/notifications.py:notify_admin_join_request`
    MUST pass `org_id` in the same PR as REQ-1..4 landing.
    """

    name: str
    email: EmailStr
    org_id: int


class JoinRequestApprovedVars(_BaseVars):
    """Variables for the `join_request_approved` email.

    REQ-3.2 design choice: `email` is an explicit schema field. The
    handler asserts `to == variables.email` before dispatch, making the
    recipient binding explicit and testable rather than an implicit
    derivation.
    """

    name: str
    email: EmailStr
    workspace_url: HttpUrl



class AutoJoinAdminNotificationVars(_BaseVars):
    """Variables for the `auto_join_admin_notification` email.

    Sent to all workspace admins when a domain_match user auto-joins.
    `admin_email` is the authoritative recipient; caller pre-resolves it.
    """

    name: str
    email: EmailStr
    domain: str
    admin_email: EmailStr

# REQ-2.2: registry keyed on template_name. Handler resolves schema via
# TEMPLATE_SCHEMAS[template_name]; unknown key -> HTTP 400.
TEMPLATE_SCHEMAS: dict[str, type[_BaseVars]] = {
    "join_request_admin": JoinRequestAdminVars,
    "join_request_approved": JoinRequestApprovedVars,
}
