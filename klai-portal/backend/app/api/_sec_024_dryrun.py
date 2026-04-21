"""SPEC-SEC-024 M4.5 — deliberate dry-run endpoint (DELETE AFTER USE).

Produces a controlled ``403 Forbidden`` from ``docker-socket-proxy`` so we can
prove the Grafana alert chain works end-to-end (AC-9):

    docker-py exec_run -> proxy denies POST /exec/*/start -> APIError is
    logged via structlog (service=portal-api) -> Alloy ships to VictoriaLogs
    -> Grafana rule "docker-socket-proxy denied a request (SPEC-SEC-024)"
    fires -> email to klai-dev-alerts-email.

Once the chain is proven in the live environment:

1. curl this endpoint once from core-01 (see .moai/specs/SPEC-SEC-024/plan.md
   section M4.5).
2. Verify the alert fires in Grafana + the email arrives.
3. Revert the commit that introduced this file (delete + router unregister).
4. Wait 30 min for the alert to auto-resolve.

The ast-grep guard ``rules/no-exec-run.yml`` explicitly ignores this file for
the duration. That ignore entry is reverted in the same cleanup commit.

Auth: reuses ``settings.internal_secret`` via ``Authorization: Bearer <…>``.
"""

from __future__ import annotations

import hmac

import docker
import docker.errors
import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal", tags=["_sec_024_dryrun"])


def _require_internal_secret(request: Request) -> None:
    """Minimal auth — mirrors the header contract of app.api.internal."""
    if not settings.internal_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal API not configured",
        )
    token = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.internal_secret}"
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.post("/_sec_024_dryrun", status_code=202)
async def sec_024_dryrun(request: Request) -> dict[str, str]:
    """Trigger one controlled Forbidden from docker-socket-proxy.

    Calls ``exec_run`` on the proxy's own container. The first docker-py
    step (``POST /containers/{id}/exec``) is allowed by CONTAINERS+POST and
    returns 201; the second step (``POST /exec/{id}/start``) is blocked by
    EXEC=0 and raises ``docker.errors.APIError: 403``. We catch it so the
    response is deterministic, and log it via structlog so Alloy picks it up
    with the portal-api service label.
    """
    _require_internal_secret(request)
    client = docker.from_env()
    try:
        ctr = client.containers.get("klai-core-docker-socket-proxy-1")
        ctr.exec_run(["true"])
    except docker.errors.APIError as e:
        logger.warning(
            "SPEC-SEC-024 M4.5 dry-run produced expected Forbidden from docker-socket-proxy",
            error=str(e),
            status_code=getattr(e.response, "status_code", None) if getattr(e, "response", None) else None,
        )
        return {"status": "expected-403-observed", "error": str(e)}

    # This branch indicates the proxy is misconfigured — EXEC was unexpectedly
    # allowed. Log hard so Grafana also surfaces it on the generic error signal.
    logger.error("SPEC-SEC-024 M4.5 dry-run SUCCEEDED — docker-socket-proxy permitted exec_run, investigate!")
    return {"status": "unexpected-success-investigate"}
