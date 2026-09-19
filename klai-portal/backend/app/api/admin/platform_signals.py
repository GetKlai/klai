"""Platform-admin overview of what the answer chain measures, and why.

Documentation, not data: this returns the list in
``app/services/observability_signals.py``, which says THAT a signal is recorded
and WHY. It never reads the logs or records themselves.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.permissions import UserPermissions, require_platform_admin
from app.services.observability_signals import SIGNALS, ObservabilitySignal

router = APIRouter(prefix="/platform/signals", tags=["platform-admin"])


@router.get("", response_model=list[ObservabilitySignal])
async def list_observability_signals(
    _perms: UserPermissions = Depends(require_platform_admin()),
) -> list[ObservabilitySignal]:
    return SIGNALS
