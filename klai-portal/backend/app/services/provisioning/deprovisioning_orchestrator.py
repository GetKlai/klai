"""Tenant deprovisioning orchestrator — SPEC-INFRA-TENANT-DELETE-001.

Entry point: `deprovision_tenant(org_id, deprovisioner_user_id, deprovisioner_type)`.
Called as a FastAPI BackgroundTask by both the owner-self-service and platform-admin
DELETE endpoints.

Failure strategy: fail-loud with Camp 1 light retries. Each of the 21 steps is
idempotent and gets 3 attempts with exponential back-off (1 s, 2 s, 4 s). On
final step failure the status transitions to `failed_deprovisioning` and
`last_failure` JSONB is populated. The admin retry endpoint can restart the
sequence from scratch — step idempotency ensures already-deleted resources are
skipped harmlessly.

# @MX:SPEC: SPEC-INFRA-TENANT-DELETE-001
# @MX:ANCHOR: deprovision_tenant — SPEC R1/R3. Entry point for both DELETE
#   endpoints. Caller must ensure org is in DEPROVISION_ENTRY_STATES before
#   scheduling this task. fan_in=2 (owner endpoint + admin endpoint).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
import docker.errors  # type: ignore[import-untyped]
import httpx
import pymongo.errors
import redis
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.provisioning_names import validate_slug_for_provisioning
from app.models.portal import PortalOrg, PortalUser
from app.services.provisioning.deprovisioning_steps import STEPS

try:
    from qdrant_client.http.exceptions import UnexpectedResponse as QdrantUnexpectedResponse
except ImportError:  # qdrant_client not installed in test env
    QdrantUnexpectedResponse = Exception  # type: ignore[assignment,misc]

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Retryable exception tuple — SPEC R3
# ---------------------------------------------------------------------------

_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.HTTPError,
    docker.errors.APIError,
    pymongo.errors.OperationFailure,
    redis.RedisError,
    asyncpg.PostgresError,
    QdrantUnexpectedResponse,
)


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------


@dataclass
class _DeprovisionState:
    """Resource handles loaded at the start of the deprovisioning run.

    All fields needed by the 21 step functions are resolved once in `_load_state`
    before any step runs. This avoids mid-sequence DB reads and keeps each step
    self-contained.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R3 — db is the orchestrator's long-lived
    #   session (not the request session). _finalize_postgres_delete commits and
    #   ends the session's useful life.
    """

    db: AsyncSession
    org_id: int
    slug: str
    zitadel_org_id: str
    zitadel_oidc_app_id: str  # "" only when the app is confirmed absent
    litellm_team_id: str  # "" only when the team is confirmed absent
    moneybird_subscription_id: str | None
    moneybird_contact_id: str | None
    deprovisioner_user_id: str
    deprovisioner_type: str  # 'owner' | 'platform_admin' | 'system'
    org_name: str
    zitadel_user_ids: tuple[str, ...]


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class DeprovisionStepError(Exception):
    """Raised by `_run_step_with_retry` when a step exceeds all retry attempts.

    Carries the step name, original exception, and the attempt count at which
    the failure occurred (1 for non-retryable, 1-3 for retryable).
    """

    def __init__(self, step_name: str, original: Exception, attempt: int = 1) -> None:
        self.step_name = step_name
        self.original = original
        self.attempt = attempt
        super().__init__(f"step {step_name} failed at attempt {attempt}: {original}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def deprovision_tenant(
    org_id: int,
    deprovisioner_user_id: str,
    deprovisioner_type: str,
) -> None:
    """FastAPI BackgroundTask entry point.

    Opens its own DB session (the request session is closed before this runs).
    All DB interactions use this session; _finalize_postgres_delete commits
    and hard-deletes the org row as its last action.

    # @MX:ANCHOR: SPEC-INFRA-TENANT-DELETE-001 R1/R3. fan_in=2.
    # @MX:NOTE: deprovisioner_type must be one of 'owner' | 'platform_admin' | 'system'.
    """
    async with AsyncSessionLocal() as db:
        await _run(org_id, deprovisioner_user_id, deprovisioner_type, db)


# ---------------------------------------------------------------------------
# Internal orchestration
# ---------------------------------------------------------------------------


async def _run(org_id: int, actor_id: str, actor_type: str, db: AsyncSession) -> None:
    """Main deprovisioning loop. Runs all steps sequentially."""
    try:
        state = await _load_state(org_id, actor_id, actor_type, db)
    except Exception as exc:
        logger.exception(
            "deprovisioning_failed",
            org_id=org_id,
            step="_load_state",
            error=str(exc),
        )
        await _mark_failed(db, org_id, "_load_state", str(exc), attempt=1)
        return

    logger.info("deprovisioning_started", org_id=org_id, slug=state.slug, actor_type=actor_type)

    try:
        for step_fn in STEPS:
            await _run_step_with_retry(step_fn, state)
    except DeprovisionStepError as exc:
        logger.exception(
            "deprovisioning_failed",
            org_id=org_id,
            slug=state.slug,
            step=exc.step_name,
            error=str(exc.original),
        )
        await _mark_failed(db, org_id, exc.step_name, str(exc.original), attempt=getattr(exc, "attempt", 1))
        return

    logger.info("deprovisioning_complete", org_id=org_id, slug=state.slug)


async def _load_state(org_id: int, actor_id: str, actor_type: str, db: AsyncSession) -> _DeprovisionState:
    """Read org row and resolve external resource IDs into state.

    Looks up LiteLLM team_id by team_alias={slug} and Zitadel OIDC app_id
    by client_id from portal_orgs. Confirmed absence is non-fatal; lookup
    failures propagate so the run lands in failed_deprovisioning.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R3 — load-once pattern. No mid-run
    #   DB reads in step functions (except _finalize_postgres_delete which owns
    #   the commit). All resource IDs captured here before any step runs.
    """
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = result.scalar_one()

    # H2 fix (SPEC-INFRA-TENANT-DELETE): prefer the IDs persisted at
    # provisioning time. Resolving by fuzzy LiteLLM/Zitadel list lookups
    # returns "" (= "confirmed absent → skip the delete") on any
    # false-negative response, silently orphaning the team/app. The stored
    # column is exact. Legacy rows provisioned before migration e9f1a2b3c4d5
    # have NULL columns and fall back to the resolve path — those tenants can
    # be back-filled by the H2 backfill script; until then the fallback keeps
    # them deletable.
    zitadel_oidc_app_id = org.zitadel_oidc_app_id
    if zitadel_oidc_app_id is None:
        zitadel_oidc_app_id = await _resolve_zitadel_oidc_app_id(
            slug=org.slug,
            client_id=org.zitadel_librechat_client_id,
        )
    litellm_team_id = org.litellm_team_id
    if litellm_team_id is None:
        litellm_team_id = await _resolve_litellm_team_id(org.slug)
    single_membership_users = (
        select(PortalUser.zitadel_user_id)
        .group_by(PortalUser.zitadel_user_id)
        .having(func.count(PortalUser.org_id) == 1)
    )
    user_result = await db.execute(
        select(PortalUser.zitadel_user_id)
        .where(
            PortalUser.org_id == org_id,
            PortalUser.zitadel_user_id.in_(single_membership_users),
        )
        .order_by(PortalUser.id)
    )
    zitadel_user_ids = tuple(dict.fromkeys(user_id for user_id in user_result.scalars().all() if user_id))

    return _DeprovisionState(
        db=db,
        org_id=org_id,
        slug=org.slug,
        zitadel_org_id=org.zitadel_org_id,
        zitadel_oidc_app_id=zitadel_oidc_app_id,
        litellm_team_id=litellm_team_id,
        moneybird_subscription_id=org.moneybird_subscription_id,
        moneybird_contact_id=org.moneybird_contact_id,
        deprovisioner_user_id=actor_id,
        deprovisioner_type=actor_type,
        org_name=org.name,
        zitadel_user_ids=zitadel_user_ids,
    )


async def _resolve_zitadel_oidc_app_id(slug: str | None, client_id: str | None = None) -> str:
    """Look up the Zitadel OIDC app_id by the tenant slug.

    Returns "" only when Zitadel confirms no matching app exists. Lookup
    failures propagate so tenant delete fails loudly instead of orphaning apps.

    SPEC-INFRA-TENANT-DELETE-003 Bug 1 fix: the previous version searched on
    ``nameQuery.name = client_id``, but the Zitadel app name is
    ``librechat-{slug}`` (not the numeric clientId). The query always
    returned zero hits, step 14 skipped silently, and the OIDC app
    was orphaned in Zitadel for every deprovisioned tenant. Confirmed
    against prod 2026-05-13 17:42:
      nameQuery name="372719472328310801" → 0 hits
      nameQuery name="librechat-e2e"      → 1 hit (the orphan)

    The optional ``client_id`` is kept as a belt+braces verification —
    if both slug and client_id are present and Zitadel returns multiple
    apps, we match on clientId. In practice the name is unique within
    a project so the verification is a no-op, but it costs nothing and
    catches a future Zitadel API surprise.

    # @MX:NOTE: app_id is NOT stored in portal_orgs (only client_id is). We must
    #   query Zitadel's app list. If the list endpoint is unavailable, abort
    #   deprovisioning so the operator can retry after Zitadel recovers.
    """
    if not slug:
        raise RuntimeError("slug is required to resolve Zitadel OIDC app id")

    expected_name = validate_slug_for_provisioning(slug, domain=settings.domain).zitadel_oidc_app_name

    async with httpx.AsyncClient(
        base_url=settings.zitadel_base_url,
        headers={"Authorization": f"Bearer {settings.zitadel_pat}"},
        timeout=10.0,
    ) as http:
        resp = await http.post(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/_search",
            json={"queries": [{"nameQuery": {"name": expected_name, "method": "APP_QUERY_METHOD_EQUALS"}}]},
        )
        resp.raise_for_status()
        apps = resp.json().get("result", [])
        for app in apps:
            # Belt+braces verification — if both clientId and slug match,
            # we are sure this is the right app. If only slug matches
            # (client_id is empty / never stored), trust the slug match.
            app_client_id = app.get("oidcConfig", {}).get("clientId", "")
            if client_id and app_client_id and app_client_id != client_id:
                continue
            return app.get("id", "")

    return ""


async def _resolve_litellm_team_id(slug: str) -> str:
    """Look up the LiteLLM team_id by team_alias={slug}.

    Returns "" only when LiteLLM confirms no matching team exists. Lookup
    failures propagate so tenant delete fails loudly instead of orphaning teams.

    # @MX:NOTE: litellm_team_id is NOT stored in portal_orgs (only litellm_team_key
    #   is stored as encrypted bytes). We resolve at deprovisioning time by querying
    #   LiteLLM's /team/list. If LiteLLM is unavailable, the HTTPError propagates
    #   and the orchestrator marks the tenant failed for retry.
    """
    async with httpx.AsyncClient(
        base_url=settings.litellm_base_url,
        headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
        timeout=10.0,
    ) as http:
        resp = await http.get("/team/list", params={"team_alias": slug})
        resp.raise_for_status()
        payload = resp.json()
        teams = payload if isinstance(payload, list) else payload.get("teams", [])
        for team in teams:
            if team.get("team_alias") == slug:
                return str(team.get("team_id", ""))

    return ""


async def _run_step_with_retry(
    step_fn,  # Callable[[_DeprovisionState], Awaitable[None]]
    state: _DeprovisionState,
) -> None:
    """Run one step with exponential back-off retry on retryable exceptions.

    3 attempts total with delays [1s, 2s, 4s]. On exhaustion raises
    `DeprovisionStepError`. Non-retryable exceptions fail immediately.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R3 — retry policy. Delays match
    #   SPEC (1, 2, 4). Non-retryable exceptions bypass all retries.
    """
    # delays[i] = sleep AFTER attempt i+1 if it failed retryably.
    # 3 total attempts; sleep 1s after attempt 1, 2s after attempt 2.
    # No sleep after attempt 3 — we fail-fast on the final exhaustion.
    delays_between = [1, 2]
    max_attempts = len(delays_between) + 1  # 3
    last_exc: Exception | None = None
    last_attempt = 0

    for attempt in range(1, max_attempts + 1):
        last_attempt = attempt
        try:
            await step_fn(state)
            return
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            logger.warning(
                "deprovisioning_step_retry",
                step=step_fn.__name__,
                slug=state.slug,
                attempt=attempt,
                error=str(exc),
            )
            if attempt < max_attempts:
                await asyncio.sleep(delays_between[attempt - 1])
        except Exception as exc:
            # Non-retryable — wrap and fail immediately. Pass real attempt
            # so _mark_failed records the correct count (typically 1).
            raise DeprovisionStepError(step_fn.__name__, exc, attempt=attempt) from exc

    raise DeprovisionStepError(step_fn.__name__, last_exc or RuntimeError("unknown"), attempt=last_attempt)


async def _mark_failed(db: AsyncSession, org_id: int, step_name: str, error_str: str, attempt: int = 1) -> None:
    """Transition org to `failed_deprovisioning` + populate last_failure JSONB.

    Best-effort: failures here are logged but not re-raised (the original
    `DeprovisionStepError` is already captured by the caller).

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R2/R5 — failed_deprovisioning is
    #   the terminal failure state. last_failure provides admin context for retry.
    """
    try:
        from app.services.provisioning.state_machine import transition_state

        await transition_state(
            db,
            org_id,
            from_state="deprovisioning",
            to_state="failed_deprovisioning",
            step=step_name,
        )
        # Populate last_failure JSONB via raw text() rather than the ORM. The
        # ORM path would layer a fresh transaction and refresh onto a session
        # that just recovered from the failure branch. `portal_orgs` is not a
        # Cat-D RLS table; keep text() here for explicit JSONB binding and
        # transactional simplicity.
        # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-003 Bug B — asyncpg cannot bind a
        #   Python dict to a jsonb column via text() prepared statements; it
        #   tries to call `.encode()` on the dict and raises DataError. Encode
        #   to a JSON string and CAST on the SQL side so the bind value is a
        #   str (which asyncpg encodes correctly). See portal-backend.md
        #   "SQLAlchemy + RLS — ::jsonb casts conflict with :param".
        failed_at = datetime.now(UTC).isoformat()
        failure_payload = json.dumps(
            {
                "step": step_name,
                "error": error_str[:500],  # truncate to keep JSONB compact
                "attempt": attempt,
                "failed_at": failed_at,
            }
        )
        await db.execute(
            text("UPDATE portal_orgs SET last_failure = CAST(:failure AS jsonb) WHERE id = :id"),
            {"failure": failure_payload, "id": org_id},
        )
        await db.commit()
        logger.info("deprovisioning_failed_state_set", org_id=org_id, step=step_name)
    except Exception:
        logger.exception("deprovisioning_mark_failed_error", org_id=org_id, step=step_name)
        with suppress(Exception):
            await db.rollback()
